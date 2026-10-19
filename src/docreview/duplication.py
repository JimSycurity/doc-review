"""Retrieve section pairs by estimated word-shingle overlap, never by meaning."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations, product
from typing import TYPE_CHECKING

from docreview.configuration import integer
from docreview.configuration import text as scalar_text
from docreview.documentation_decisions import as_object
from docreview.frontmatter import validate_document
from docreview.index_cache import read_cache, write_cache
from docreview.markdown import sections
from docreview.paths import matches

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from docreview.configuration import ProjectConfig
    from docreview.documentation_decisions import Json

ALGORITHM = "minhash64-word5-v1"
SIGNATURE_SIZE = 64
PRIME = (1 << 61) - 1


@dataclass(frozen=True)
class SectionSignature:
    """Store only a location and hashes; full text stays in the source document."""

    path: str
    heading: str
    line_start: int
    line_end: int
    signature: tuple[int, ...]
    shingles: int

    def cache_record(self) -> dict[str, Json]:
        signature: list[Json] = []
        signature.extend(self.signature)
        return {**self.location(), "signature": signature, "shingles": self.shingles}

    def location(self) -> dict[str, Json]:
        return {
            "path": self.path,
            "heading": self.heading,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass(frozen=True)
class DuplicatePair:
    """Lexical similarity is retrieval evidence, not a duplication verdict."""

    left: SectionSignature
    right: SectionSignature
    estimated_overlap: float

    def as_json(self) -> dict[str, Json]:
        return {
            "left": self.left.location(),
            "right": self.right.location(),
            "estimated_overlap": self.estimated_overlap,
        }


@dataclass(frozen=True)
class DuplicationIndex:
    """Keep the corpus identity and retrieval results separate from model judgment."""

    tree_sha256: str
    records: tuple[SectionSignature, ...]
    skipped_sections: int
    cache_hit: bool = False
    cache_error: str | None = None

    def candidates(self, selected: set[str], threshold: float) -> list[DuplicatePair]:
        """Count matching MinHash coordinates exactly; no lossy LSH shortlist."""
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("overlap threshold must be a finite fraction")
        grouped: defaultdict[tuple[int, ...], list[SectionSignature]] = defaultdict(
            list
        )
        for record in self.records:
            grouped[record.signature].append(record)
        groups = list(grouped.values())
        counts = _group_matches(groups)
        pairs = []
        for group in groups:
            pairs.extend(_selected_pairs(combinations(group, 2), selected, 1.0))
        group_pairs = combinations(range(len(groups)), 2) if threshold == 0 else counts
        for left, right in group_pairs:
            overlap = counts.get((left, right), 0) / SIGNATURE_SIZE
            if overlap >= threshold:
                pairs.extend(
                    _selected_pairs(
                        product(groups[left], groups[right]), selected, overlap
                    )
                )
        return sorted(
            pairs,
            key=lambda pair: (
                pair.left.path,
                pair.left.line_start,
                pair.right.path,
                pair.right.line_start,
            ),
        )

    def metadata(self) -> dict[str, Json]:
        return {
            "algorithm": ALGORITHM,
            "tree_sha256": self.tree_sha256,
            "sections_indexed": len(self.records),
            "sections_skipped": self.skipped_sections,
            "cache_hit": self.cache_hit,
            "cache_error": self.cache_error,
        }


def _selected_pairs(
    pairs: Iterable[tuple[SectionSignature, SectionSignature]],
    selected: set[str],
    overlap: float,
) -> list[DuplicatePair]:
    result = []
    for left, right in pairs:
        if left.path in selected or right.path in selected:
            if (left.path, left.line_start) > (right.path, right.line_start):
                left, right = right, left
            result.append(DuplicatePair(left, right, overlap))
    return result


def _group_matches(groups: list[list[SectionSignature]]) -> Counter[tuple[int, int]]:
    postings: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
    for group_id, group in enumerate(groups):
        for coordinate, value in enumerate(group[0].signature):
            postings[coordinate, value].append(group_id)
    counts: Counter[tuple[int, int]] = Counter()
    for ids in postings.values():
        counts.update(combinations(ids, 2))
    return counts


@lru_cache(maxsize=1)
def _permutations() -> tuple[tuple[int, int], ...]:
    result = []
    for coordinate in range(SIGNATURE_SIZE):
        seed = hashlib.sha256(f"{ALGORITHM}:{coordinate}".encode()).digest()
        result.append(
            (
                int.from_bytes(seed[:8]) % (PRIME - 1) + 1,
                int.from_bytes(seed[8:16]) % PRIME,
            )
        )
    return tuple(result)


def _signature(text: str) -> tuple[tuple[int, ...], int]:
    words = re.findall(r"\w+", text.casefold())
    hashes = {
        int.from_bytes(
            hashlib.blake2b(
                " ".join(words[start : start + 5]).encode(), digest_size=8
            ).digest()
        )
        % PRIME
        for start in range(len(words) - 4)
    }
    if not hashes:
        return (), 0
    return tuple(
        min((a * value + b) % PRIME for value in hashes) for a, b in _permutations()
    ), len(hashes)


def _tree_hash(corpus: Mapping[str, str], starts: Mapping[str, int]) -> str:
    digest = hashlib.sha256(ALGORITHM.encode())
    for path, text in sorted(corpus.items()):
        for value in (path.encode(), str(starts[path]).encode(), text.encode()):
            digest.update(len(value).to_bytes(8))
            digest.update(value)
    return digest.hexdigest()


def _records(
    corpus: Mapping[str, str], starts: Mapping[str, int]
) -> tuple[tuple[SectionSignature, ...], int]:
    records = []
    skipped = 0
    for path, text in sorted(corpus.items()):
        lines = text.splitlines()
        for section in sections(text, starts[path]):
            signature, shingles = _signature(
                "\n".join(lines[section.line_start - 1 : section.line_end])
            )
            if not signature:
                skipped += 1
                continue
            records.append(
                SectionSignature(
                    path,
                    section.heading,
                    section.line_start,
                    section.line_end,
                    signature,
                    shingles,
                )
            )
    return tuple(records), skipped


def load_index(
    config: ProjectConfig,
    corpus: Mapping[str, str],
    starts: Mapping[str, int] | None = None,
) -> DuplicationIndex:
    """Index only maintained Markdown, using fixed deterministic permutations."""
    selected = {
        path: text
        for path, text in corpus.items()
        if path.endswith(".md")
        and matches(path, config.maintained)
        and not matches(path, config.excluded)
    }
    body_starts = {
        path: starts[path]
        if starts is not None
        else validate_document(config, path, text).body_start
        for path, text in selected.items()
    }
    tree_hash = _tree_hash(selected, body_starts)
    cache_error = None
    try:
        cached_payload = read_cache(config, tree_hash)
        if cached_payload is not None:
            return _from_cache(cached_payload, tree_hash, selected)
    except (OSError, ValueError, TypeError) as exc:
        cache_error = f"cache ignored ({type(exc).__name__}); index rebuilt"
    records, skipped = _records(selected, body_starts)
    index = DuplicationIndex(tree_hash, records, skipped, cache_error=cache_error)
    payload: dict[str, Json] = {
        "algorithm": ALGORITHM,
        "tree_sha256": tree_hash,
        "sections": [record.cache_record() for record in records],
        "skipped_sections": skipped,
    }
    try:
        write_cache(config, tree_hash, payload)
    except (OSError, ValueError) as exc:
        index = DuplicationIndex(
            tree_hash,
            records,
            skipped,
            cache_error=f"cache write unavailable ({type(exc).__name__}); index computed in memory",
        )
    return index


def _from_cache(
    payload: dict[str, Json], tree_hash: str, corpus: Mapping[str, str]
) -> DuplicationIndex:
    if (
        set(payload) != {"algorithm", "tree_sha256", "sections", "skipped_sections"}
        or payload["algorithm"] != ALGORITHM
        or payload["tree_sha256"] != tree_hash
    ):
        raise ValueError("index cache version or corpus mismatch")
    raw_records = payload["sections"]
    if not isinstance(raw_records, list):
        raise TypeError("index sections must be a list")
    records = tuple(
        _cached_record(as_object(row, "section"), corpus) for row in raw_records
    )
    if len({(record.path, record.line_start) for record in records}) != len(records):
        raise ValueError("index has repeated section locations")
    return DuplicationIndex(
        tree_hash, records, integer(payload, "skipped_sections", 0), cache_hit=True
    )


def _cached_record(row: dict[str, Json], corpus: Mapping[str, str]) -> SectionSignature:
    if set(row) != {
        "path",
        "heading",
        "line_start",
        "line_end",
        "signature",
        "shingles",
    }:
        raise ValueError("unknown section cache fields")
    path = scalar_text(row, "path")
    if path not in corpus:
        raise ValueError("section path is not maintained")
    start, end = integer(row, "line_start", 0), integer(row, "line_end", 0)
    if (
        not 1 <= start <= end <= len(corpus[path].splitlines())
        or integer(row, "shingles", 0) < 1
    ):
        raise ValueError("section cache location or shingle count is invalid")
    values = row["signature"]
    if not isinstance(values, list) or len(values) != SIGNATURE_SIZE:
        raise TypeError("section cache needs a complete signature")
    signature = tuple(
        integer({"coordinate": value}, "coordinate", 0) for value in values
    )
    if any(value >= PRIME for value in signature):
        raise ValueError("signature coordinate is out of range")
    return SectionSignature(
        path,
        scalar_text(row, "heading"),
        start,
        end,
        signature,
        integer(row, "shingles", 0),
    )
