import argparse

import pytest

from orchestragent.server import build_routing


def args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "transport": "http",
        "implement_endpoint": None,
        "review_endpoint": None,
        "implement_model": "",
        "review_model": "",
        "implement_key_env": "",
        "review_key_env": "",
        "implement_command": None,
        "review_command": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_no_configuration_stays_on_mock() -> None:
    registry, routes = build_routing(args())
    assert registry.ids() == ("mock",)
    assert set(routes.values()) == {"mock"}


def test_http_transport_requires_both_sides() -> None:
    with pytest.raises(SystemExit, match="Both --implement-endpoint"):
        build_routing(args(implement_endpoint="http://a.example"))


def test_http_transport_registers_two_distinct_providers() -> None:
    registry, routes = build_routing(
        args(
            implement_endpoint="http://a.example",
            review_endpoint="http://b.example",
            implement_model="ma",
            review_model="mb",
        )
    )
    assert registry.ids() == ("mock", "implement", "review")
    assert (routes["implement"], routes["review"]) == ("implement", "review")


def test_cli_transport_requires_both_sides() -> None:
    with pytest.raises(SystemExit, match="Both --implement-command"):
        build_routing(args(transport="cli", review_command="tool review"))


def test_cli_transport_registers_two_distinct_providers() -> None:
    registry, routes = build_routing(
        args(transport="cli", implement_command="tool write", review_command="tool check")
    )
    assert registry.ids() == ("mock", "implement", "review")
    assert routes["plan"] == "implement"
    assert routes["review"] == "review"
