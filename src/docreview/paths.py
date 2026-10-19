"""Match repository-relative gitignore-style globs without following external paths."""

from __future__ import annotations

import fnmatch
import os
from functools import lru_cache
from typing import TYPE_CHECKING

from docreview.configuration import relative_path

if TYPE_CHECKING:
    from pathlib import Path

    from docreview.configuration import ProjectConfig


@lru_cache(maxsize=4096)
def _segments(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    if not pattern:
        return not parts
    if pattern[0] == "**":
        return _segments(parts, pattern[1:]) or bool(
            parts and _segments(parts[1:], pattern)
        )
    return bool(
        parts
        and fnmatch.fnmatchcase(parts[0], pattern[0])
        and _segments(parts[1:], pattern[1:])
    )


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    """Apply ordered patterns; ** spans zero or more directories, * stays in one."""
    matched = False
    for raw in patterns:
        pattern = raw.removeprefix("!")
        anchored = pattern.startswith("/")
        pattern = pattern.lstrip("/")
        if pattern.endswith("/"):
            pattern += "**"
        parts = tuple(path.split("/"))
        fits = (
            _segments(parts, tuple(pattern.split("/")))
            if "/" in pattern or anchored
            else any(fnmatch.fnmatchcase(part, pattern) for part in parts)
        )
        if fits:
            matched = not raw.startswith("!")
    return matched


def repository_path(root: Path, path: str) -> Path:
    """Reject outside symlinks before reading any configured source or link."""
    target = root / relative_path(path)
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"path leaves repository: {path}")
    return target


def maintained_paths(config: ProjectConfig) -> list[str]:
    """Walk Markdown only; skip tool environments and excluded directory trees."""
    result = []
    for parent, dirs, files in os.walk(config.root, followlinks=False):
        prefix = (config.root / parent).relative_to(config.root).as_posix()
        prefix = "" if prefix == "." else prefix + "/"
        dirs[:] = [
            name
            for name in dirs
            if name not in {".git", ".venv", "node_modules", "__pycache__"}
            and not matches(prefix + name + "/", config.excluded)
        ]
        for name in files:
            path = prefix + name
            if (
                name.endswith(".md")
                and matches(path, config.maintained)
                and not matches(path, config.excluded)
            ):
                repository_path(config.root, path)
                result.append(path)
    return sorted(result)
