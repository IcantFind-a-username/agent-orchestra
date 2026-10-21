"""Loopback-only service for the durable orchestration core."""

import argparse
import fcntl
import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from orchestragent.controller import Controller
from orchestragent.serialization import dumps
from orchestragent.store import Conflict, Store
from orchestragent.workspace import Workspace


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, controller: Controller, port: int = 8765):
        self.controller = controller
        self.mode = "mock"
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(".data"))
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    with (args.data_dir / "controller.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another controller owns this data directory") from None
        controller = Controller(
            Store(args.data_dir / "tasks.sqlite"),
            Workspace(args.data_dir / "workspaces"),
            lease_seconds=30,
        )
        # Bind before recovering: a second server must not invalidate active work.
        server = Server(controller, args.port)
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
