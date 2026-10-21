import sqlite3
from pathlib import Path

import pytest

from orchestragent.store import MIGRATIONS, Store


def version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def indexes(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        connection.close()


def test_a_fresh_database_reaches_the_latest_version(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite"
    Store(path)
    assert version(path) == len(MIGRATIONS)
    assert {"events_task", "events_kind"} <= indexes(path)


def test_reopening_does_not_replay_migrations(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite"
    Store(path)
    with pytest.raises(sqlite3.OperationalError):
        # A duplicate index would raise; replaying v1's CREATE TABLE would not,
        # so assert on a statement that must run exactly once.
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE INDEX events_kind ON events(kind, seq)")
        finally:
            connection.close()
    Store(path)
    assert version(path) == len(MIGRATIONS)


def test_an_older_database_is_upgraded_in_place(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite"
    Store(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP INDEX events_kind")
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    finally:
        connection.close()
    assert "events_kind" not in indexes(path)

    Store(path)
    assert version(path) == len(MIGRATIONS)
    assert "events_kind" in indexes(path)


def test_a_newer_database_is_refused_rather_than_corrupted(tmp_path: Path) -> None:
    path = tmp_path / "tasks.sqlite"
    Store(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA user_version={len(MIGRATIONS) + 1}")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="newer than this build"):
        Store(path)
