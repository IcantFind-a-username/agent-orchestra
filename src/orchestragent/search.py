"""Regex search over a repository, bounded by an explicit output budget.

Deliberately dependency-free: a repository is walked once, obvious build and
VCS directories are skipped, and results stop at a byte budget so an agent
gets a usable slice instead of an unreadable dump.
"""

import re
from pathlib import Path
from typing import TypedDict


class Match(TypedDict):
    path: str
    line: int
    text: str


class SearchResult(TypedDict):
    matches: list[Match]
    truncated: bool
    files_scanned: int


class PlanContext(TypedDict):
    files: list[str]
    hits: list[Match]
    query: list[str]

SKIP_DIRS = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache",
     "build", "dist", ".ruff_cache"}
)
MAX_FILE_BYTES = 256_000
MAX_LINE_CHARS = 300


def search(
    root: Path, query: str, *, limit: int = 50, max_bytes: int = 4000
) -> SearchResult:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query must contain 1 or more characters")
    if not (1 <= limit <= 200):
        raise ValueError("limit must be between 1 and 200")
    try:
        pattern = re.compile(query)
    except re.error:
        raise ValueError("Invalid regular expression") from None
    if not root.is_dir():
        raise ValueError("Search root is not a directory")

    matches: list[Match] = []
    used = 0
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        for number, line in enumerate(text.splitlines(), 1):
            if not pattern.search(line):
                continue
            excerpt = line[:MAX_LINE_CHARS]
            size = len(excerpt.encode("utf-8"))
            if len(matches) >= limit or used + size > max_bytes:
                return {"matches": matches, "truncated": True, "files_scanned": scanned}
            matches.append({"path": str(relative), "line": number, "text": excerpt})
            used += size
    return {"matches": matches, "truncated": False, "files_scanned": scanned}


WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def plan_context(
    root: Path, prompt: str, *, limit: int = 40, max_bytes: int = 4000
) -> PlanContext:
    """A budgeted view of the repository for the planning stage.

    File names give the shape; matches for the prompt's own identifiers give
    the likely touch points. Both are bounded, so a large tree cannot flood
    the planning prompt.
    """
    files = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
    )[:500]
    words = WORD.findall(prompt)[:5]
    hits: list[Match] = []
    if words:
        pattern = "|".join(sorted({re.escape(word) for word in words}))
        hits = search(root, pattern, limit=limit, max_bytes=max_bytes)["matches"]
    return {"files": files, "hits": hits, "query": words}

