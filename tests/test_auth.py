import http.client
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from orchestragent.controller import Controller
from orchestragent.server import Server, authorized
from orchestragent.store import Store
from orchestragent.workspace import Workspace


@pytest.mark.parametrize(
    ("header", "token", "expected"),
    [
        (None, "", True),
        ("anything", "", True),
        (None, "s3cret", False),
        ("", "s3cret", False),
        ("Bearer wrong", "s3cret", False),
        ("s3cret", "s3cret", False),
        ("Bearer s3cret", "s3cret", True),
    ],
)
def test_only_an_exact_bearer_matches(header: str | None, token: str, expected: bool) -> None:
    assert authorized(header, token) is expected


@pytest.fixture
def server(tmp_path: Path) -> Iterator[Server]:
    controller = Controller(Store(tmp_path / "t.sqlite"), Workspace(tmp_path / "w"))
    instance = Server(controller, 0, token="s3cret")
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def request(server: Server, path: str, token: str = "") -> int:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        connection.request("GET", path, headers=headers)
        return connection.getresponse().status
    finally:
        connection.close()


def test_health_is_reachable_without_a_token(server: Server) -> None:
    assert request(server, "/health") == 200


@pytest.mark.parametrize("path", ["/tasks", "/settings", "/providers"])
def test_everything_else_requires_the_token(server: Server, path: str) -> None:
    assert request(server, path) == 401
    assert request(server, path, token="wrong") == 401
    assert request(server, path, token="s3cret") == 200
