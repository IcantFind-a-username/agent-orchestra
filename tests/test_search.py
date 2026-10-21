from pathlib import Path

import pytest

from orchestragent.search import search


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("first\nNEEDLE one\nlast\n")
    nested = tmp_path / "pkg"
    nested.mkdir()
    (nested / "b.py").write_text("NEEDLE two\n")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("NEEDLE hidden\n")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfeNEEDLE")
    return tmp_path


def test_matches_carry_path_and_line(repo: Path) -> None:
    result = search(repo, "NEEDLE")
    matches = result["matches"]
    assert isinstance(matches, list)
    assert [(item["path"], item["line"]) for item in matches] == [
        ("a.py", 2),
        ("pkg/b.py", 1),
    ]
    assert result["truncated"] is False


def test_vcs_and_binary_files_are_skipped(repo: Path) -> None:
    paths = {item["path"] for item in search(repo, "NEEDLE")["matches"]}
    assert not any(path.startswith(".git") for path in paths)
    assert "blob.bin" not in paths


def test_the_byte_budget_truncates_instead_of_truncating_silently(repo: Path) -> None:
    result = search(repo, "NEEDLE", max_bytes=12)
    assert result["truncated"] is True
    assert result["matches"]


def test_the_match_limit_is_respected(repo: Path) -> None:
    result = search(repo, "NEEDLE", limit=1)
    assert result["truncated"] is True
    assert len(result["matches"]) == 1


def test_regular_expressions_work(repo: Path) -> None:
    assert len(search(repo, r"NEEDLE\s\w+")["matches"]) == 2


@pytest.mark.parametrize(
    ("query", "reason"),
    [("", "1 or more characters"), ("(", "Invalid regular expression")],
)
def test_bad_queries_are_rejected(repo: Path, query: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        search(repo, query)


def test_a_missing_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        search(tmp_path / "nope", "x")


def test_limit_is_bounded(repo: Path) -> None:
    with pytest.raises(ValueError, match="between 1 and 200"):
        search(repo, "x", limit=0)
