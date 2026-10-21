"""Loopback-only service for the durable orchestration core."""

import argparse
import fcntl
import hmac
import json
import os
import shlex
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from orchestragent.adapters import Adapter, MockAdapter
from orchestragent.cli_adapter import CliAdapter, CliEndpoint
from orchestragent.controller import Controller
from orchestragent.http_adapter import HttpAdapter, HttpEndpoint
from orchestragent.models import STAGES
from orchestragent.registry import ProviderRegistry
from orchestragent.search import search as search_repo
from orchestragent.serialization import dumps
from orchestragent.store import Conflict, Store
from orchestragent.workspace import Workspace


def authorized(header: str | None, token: str) -> bool:
    """No token configured means loopback trust; otherwise require an exact bearer."""
    if not token:
        return True
    return hmac.compare_digest(header or "", f"Bearer {token}")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        controller: Controller,
        port: int = 8765,
        token: str = "",
        search_root: Path | None = None,
    ):
        self.controller = controller
        self.mode = "mock"
        self.token = token
        self.search_root = search_root
        super().__init__(("127.0.0.1", port), Handler)

    def service_actions(self) -> None:
        self.controller.expire_leases()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, format: str, *args: object) -> None:
        # HTTP paths, bodies, and exception text are deliberately not logged.
        pass

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def reply(self, code: int, body: object) -> None:
        payload = dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def dispatch(self, method: str) -> None:
        host = self.headers.get("Host", "")
        allowed_hosts = {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }
        if host not in allowed_hosts or self.headers.get("Origin") is not None:
            self.reply(403, {"error": "Only native loopback clients are allowed"})
            return
        try:
            path = urlsplit(self.path)
            parts = path.path.strip("/").split("/")
            if parts != ["health"] and not authorized(
                self.headers.get("Authorization"), self.server.token
            ):
                self.reply(401, {"error": "Missing or invalid token"})
                return
            body: dict[str, object] = {}
            if method == "POST":
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Transfer-Encoding is not supported")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384:
                    raise ValueError("Request body must contain 1–16384 bytes")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected application/json")
                value = json.loads(self.rfile.read(size))
                if not isinstance(value, dict):
                    raise ValueError("Expected a JSON object")
                body = value
            controller = self.server.controller
            if method == "GET" and parts == ["health"]:
                self.reply(200, {"mode": self.server.mode, "version": 2})
            elif method == "GET" and parts == ["search"]:
                root = self.server.search_root
                if root is None:
                    raise ValueError("Search is not configured; start with --search-root")
                params = parse_qs(path.query)
                query = (params.get("q") or [""])[0]
                limit = int((params.get("limit") or ["50"])[0])
                self.reply(200, search_repo(root, query, limit=limit))
            elif method == "GET" and parts == ["providers"]:
                self.reply(200, controller.registry.status())
            elif parts == ["settings"]:
                if method == "GET":
                    self.reply(200, {**controller.settings.read(), "mode": self.server.mode})
                else:
                    self.keys(body, {"base_revision", "policy"})
                    revision = body.get("base_revision")
                    if type(revision) is not int:
                        raise ValueError("Expected revision integer")
                    self.reply(
                        200,
                        {
                            **controller.settings.update(revision, body.get("policy")),
                            "mode": self.server.mode,
                        },
                    )
            elif method == "POST" and parts == ["settings", "proposals"]:
                self.keys(body, {"instruction"})
                instruction = self.string(body, "instruction")
                proposal = controller.settings.propose(instruction)
                self.reply(200, proposal)
            elif (
                method == "POST"
                and len(parts) == 4
                and parts[:2] == ["settings", "proposals"]
                and parts[3] == "apply"
            ):
                self.keys(body, set())
                self.reply(200, {**controller.settings.apply(parts[2]), "mode": self.server.mode})
            elif parts == ["tasks"]:
                if method == "GET":
                    self.reply(200, {"tasks": [asdict(t) for t in controller.store.list_tasks()]})
                else:
                    self.keys(body, {"request_id", "prompt", "scenario", "mode"})
                    task, created = controller.submit(
                        self.string(body, "request_id"),
                        self.string(body, "prompt"),
                        self.string(body, "scenario", "happy"),
                        self.string(body, "mode", self.server.mode),
                    )
                    self.reply(201 if created else 200, asdict(task))
            elif len(parts) >= 2 and parts[0] == "tasks":
                task_id = parts[1]
                if method == "GET" and len(parts) == 2:
                    self.reply(200, asdict(controller.store.read(task_id)))
                elif method == "GET" and len(parts) == 3 and parts[2] == "events":
                    after = int(parse_qs(path.query).get("after", ["0"])[0])
                    if after < 0:
                        raise ValueError("Invalid event cursor")
                    events = controller.store.events(task_id, after)
                    self.reply(
                        200,
                        {
                            "events": [asdict(e) for e in events],
                            "cursor": events[-1].seq if events else after,
                        },
                    )
                elif method == "POST" and len(parts) == 3 and parts[2] == "actions":
                    self.keys(body, {"action"})
                    task = controller.action(task_id, self.string(body, "action"))
                    self.reply(200, asdict(task))
                elif method == "POST" and len(parts) == 4 and parts[2] == "approvals":
                    self.keys(body, {"decision", "operation_digest"})
                    task = controller.approve(
                        task_id,
                        parts[3],
                        self.string(body, "decision"),
                        self.string(body, "operation_digest"),
                    )
                    self.reply(200, asdict(task))
                else:
                    self.reply(404, {"error": "Endpoint not found"})
            else:
                self.reply(404, {"error": "Endpoint not found"})
        except Conflict as error:
            self.reply(409, {"error": str(error)})
        except KeyError:
            self.reply(404, {"error": "Task not found"})
        except (ValueError, UnicodeError):
            self.reply(400, {"error": "Invalid request; check the API contract"})
        except Exception:
            self.reply(500, {"error": "Internal error; no billing fallback was attempted"})

    @staticmethod
    def string(body: dict[str, object], key: str, default: str | None = None) -> str:
        value = body.get(key, default)
        if not isinstance(value, str):
            raise ValueError("Expected a string")
        return value

    @staticmethod
    def keys(body: dict[str, object], allowed: set[str]) -> None:
        if body.keys() - allowed:
            raise ValueError("Unexpected fields")


def build_routing(args: argparse.Namespace) -> tuple[ProviderRegistry, dict[str, str]]:
    """Mock by default; two transports when both sides are configured.

    Implementation and review are separate adapters by construction, so no task
    can ever have one endpoint approve its own work.
    """
    routes = dict.fromkeys(STAGES, "mock")
    if args.transport == "cli":
        commands = (args.implement_command, args.review_command)
        if not all(commands):
            if any(commands):
                raise SystemExit("Both --implement-command and --review-command are required")
            return ProviderRegistry([MockAdapter()]), routes
        adapters: list[Adapter] = [
            MockAdapter(),
            CliAdapter("implement", CliEndpoint(tuple(shlex.split(args.implement_command)))),
            CliAdapter("review", CliEndpoint(tuple(shlex.split(args.review_command)))),
        ]
    else:
        endpoints = (args.implement_endpoint, args.review_endpoint)
        if not all(endpoints):
            if any(endpoints):
                raise SystemExit("Both --implement-endpoint and --review-endpoint are required")
            return ProviderRegistry([MockAdapter()]), routes
        adapters = [
            MockAdapter(),
            HttpAdapter(
                "implement",
                HttpEndpoint(
                    base_url=args.implement_endpoint,
                    model=args.implement_model,
                    api_key=os.environ.get(args.implement_key_env, "")
                    if args.implement_key_env
                    else "",
                ),
            ),
            HttpAdapter(
                "review",
                HttpEndpoint(
                    base_url=args.review_endpoint,
                    model=args.review_model,
                    api_key=os.environ.get(args.review_key_env, "")
                    if args.review_key_env
                    else "",
                ),
            ),
        ]
    routes.update({"plan": "implement", "implement": "implement", "review": "review"})
    return ProviderRegistry(adapters), routes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(".data"))
    parser.add_argument("--search-root", type=Path, help="Directory exposed to /search")
    parser.add_argument(
        "--seed-repo",
        type=Path,
        help="Existing repository a task starts from, instead of an empty workspace",
    )
    parser.add_argument(
        "--token-env",
        default="",
        help="Env var holding a bearer token; required when the port is not private",
    )
    parser.add_argument(
        "--transport",
        choices=("http", "cli"),
        default="http",
        help="How the two sides are reached: an HTTP endpoint or a local command",
    )
    parser.add_argument("--implement-command", help="Command that implements (no shell)")
    parser.add_argument("--review-command", help="Command that reviews (no shell)")
    parser.add_argument("--implement-endpoint", help="Base URL of the implementing endpoint")
    parser.add_argument("--implement-model", default="")
    parser.add_argument("--implement-key-env", default="", help="Env var holding the API key")
    parser.add_argument("--review-endpoint", help="Base URL of the reviewing endpoint")
    parser.add_argument("--review-model", default="")
    parser.add_argument("--review-key-env", default="", help="Env var holding the API key")
    parser.add_argument(
        "--allow-unsandboxed-tests",
        action="store_true",
        help="Run model-authored tests without an OS sandbox (unsafe; off by default)",
    )
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    with (args.data_dir / "controller.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another controller owns this data directory") from None
        registry, routes = build_routing(args)
        controller = Controller(
            Store(args.data_dir / "tasks.sqlite"),
            Workspace(args.data_dir / "workspaces", args.seed_repo),
            registry=registry,
            routes=routes,
            lease_seconds=30,
            allow_bundled_tests=args.allow_unsandboxed_tests,
        )
        # Bind before recovering: a second server must not invalidate active work.
        token = os.environ.get(args.token_env, "") if args.token_env else ""
        server = Server(controller, args.port, token, args.search_root)
        controller.recover()
        stopped = threading.Event()

        def work() -> None:
            while not stopped.is_set():
                controller.tick()
                stopped.wait(0.35)

        worker = threading.Thread(target=work, daemon=True, name="dispatch-worker")
        worker.start()
        print(f"{server.mode}: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
        finally:
            stopped.set()
            server.server_close()
            worker.join(timeout=10)


if __name__ == "__main__":
    main()
