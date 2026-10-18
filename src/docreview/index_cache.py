"""Read checked index payloads and atomically replace disposable local caches."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from docreview.documentation_decisions import Json, as_object, read_json
from docreview.paths import repository_path

if TYPE_CHECKING:
    from docreview.configuration import ProjectConfig


def _encoded(payload: dict[str, Json]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _cache_file(config: ProjectConfig, tree_hash: str) -> Path:
    file = repository_path(config.root, f".tmp/docreview/duplicates/{tree_hash}.json")
    if file.is_symlink():
        raise ValueError("cache file cannot be a symlink")
    return file


def read_cache(config: ProjectConfig, tree_hash: str) -> dict[str, Json] | None:
    """Treat cache contents as external data, not proof of a completed assessment."""
    file = _cache_file(config, tree_hash)
    if not file.exists():
        return None
    envelope = as_object(read_json(file.read_text(encoding="utf-8")), "index cache")
    if set(envelope) != {"sha256", "index"}:
        raise ValueError("unknown index cache envelope")
    payload = as_object(envelope["index"], "index")
    if envelope["sha256"] != hashlib.sha256(_encoded(payload)).hexdigest():
        raise ValueError("index cache checksum mismatch")
    return payload


def write_cache(
    config: ProjectConfig, tree_hash: str, payload: dict[str, Json]
) -> None:
    """A contributed repository never receives even a cache-directory write."""
    if config.authority != "owned":
        return
    file = _cache_file(config, tree_hash)
    file.parent.mkdir(parents=True, exist_ok=True)
    envelope: dict[str, Json] = {
        "sha256": hashlib.sha256(_encoded(payload)).hexdigest(),
        "index": payload,
    }
    with TemporaryDirectory(prefix=".docreview-", dir=file.parent) as directory:
        temporary = Path(directory) / "index.json"
        temporary.write_bytes(_encoded(envelope))
        temporary.replace(file)
