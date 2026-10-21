import http.client
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
Launch = Callable[[], subprocess.Popen[bytes]]


def call(port: int, method: str, path: str, body: object = None) -> dict[str, object]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(
            method,
            path,
            json.dumps(body) if body is not None else None,
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        data: dict[str, object] = json.loads(response.read())
        if response.status >= 400:
            raise AssertionError(f"Unexpected HTTP {response.status}")
        return data
    finally:
        connection.close()


def until(check: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.05)
    raise AssertionError("Local Mock service did not reach expected state")


@pytest.fixture
def service(tmp_path: Path) -> Iterator[tuple[int, Launch]]:
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    processes: list[subprocess.Popen[bytes]] = []
    log = (tmp_path / "server.log").open("ab")

    def launch() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "orchestragent.server",
                "--port",
                str(port),
                "--data-dir",
                str(tmp_path / "data"),
            ],
            cwd=ROOT,
            env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src")},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        processes.append(process)
        return process

    try:
        yield port, launch
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()
        # Shown only when a test fails: explains why the service never came up.
        print((tmp_path / "server.log").read_bytes().decode(errors="replace"), file=sys.stderr)


def test_process_kill_and_restart_preserves_approval(service: tuple[int, Launch]) -> None:
    port, launch = service
    first = launch()
    until(lambda: call(port, "GET", "/health")["mode"] == "mock")
    request = {
        "request_id": "restart",
        "prompt": "Recover after process kill",
        "scenario": "approval",
    }
    task = call(port, "POST", "/tasks", request)
    path = f"/tasks/{task['id']}"
    until(lambda: call(port, "GET", path)["status"] == "waiting_approval")
    before = call(port, "GET", path)
    first.kill()
    first.wait(timeout=5)
    launch()
    until(lambda: call(port, "GET", "/health")["mode"] == "mock")
    duplicate = call(port, "POST", "/tasks", request)
    approval = duplicate["approval"]
    assert isinstance(approval, dict)
    assert (duplicate["id"], approval, duplicate["status"]) == (
        before["id"],
        before["approval"],
        "waiting_approval",
    )
    call(
        port,
        "POST",
        f"{path}/approvals/{approval['id']}",
        {
            "decision": "approve",
            "operation_digest": approval["operation_digest"],
        },
    )
    until(lambda: call(port, "GET", path)["status"] == "completed")
    assert call(port, "GET", path)["cost_usd"] == 0


def test_second_server_cannot_recover_first_servers_tasks(service: tuple[int, Launch]) -> None:
    port, launch = service
    first = launch()
    until(lambda: call(port, "GET", "/health")["mode"] == "mock")
    second = launch()
    code = second.wait(timeout=5)
    assert (code != 0, first.poll(), call(port, "GET", "/health")["mode"]) == (True, None, "mock")
