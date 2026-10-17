"""Extract located Markdown structure and links without treating code as prose."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docreview.documentation_decisions import Json


@dataclass(frozen=True)
class Section:
    """A heading's direct content; child sections have their own records."""

    heading: str
    level: int
    line_start: int
    line_end: int
    words: int

    def as_json(self) -> dict[str, Json]:
        return {
            "heading": self.heading,
            "level": self.level,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "words": self.words,
        }


@dataclass(frozen=True)
class Link:
    """Keep the source line and target separate for graph and error reporting."""

    target: str
    line: int


def prose_lines(
    text: str, body_start: int = 1, *, mask_inline: bool = True
) -> list[tuple[int, str]]:
    """Mask fenced and inline code; retain line numbers for every finding."""
    result = []
    fence: tuple[str, int] | None = None
    for number, line in enumerate(text.splitlines(), 1):
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening:
            marker = opening[1]
            fence = _fence_state(fence, marker, line[opening.end() :])
            continue
        if number >= body_start and fence is None:
            result.append(
                (number, re.sub(r"(`+).*?\1", "", line) if mask_inline else line)
            )
    return result


def _fence_state(
    fence: tuple[str, int] | None, marker: str, tail: str
) -> tuple[str, int] | None:
    if fence is None:
        return marker[0], len(marker)
    if marker[0] == fence[0] and len(marker) >= fence[1] and not tail.strip():
        return None
    return fence


def sections(text: str, body_start: int = 1) -> list[Section]:
    """Use ATX and setext headings, ignoring heading-shaped examples in fences."""
    lines = text.splitlines()
    visible = prose_lines(text, body_start, mask_inline=False)
    headings: list[tuple[int, str, int]] = []
    previous = ""
    for number, line in visible:
        match = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((number, match[2], len(match[1])))
        elif previous.strip() and re.fullmatch(r" {0,3}(=+|-+)\s*", line):
            headings.append((number - 1, previous.strip(), 1 if "=" in line else 2))
        previous = line
    if not headings or headings[0][0] > body_start:
        headings.insert(0, (body_start, "Opening", 0))
    result = []
    for index, (start, title, level) in enumerate(headings):
        end = headings[index + 1][0] - 1 if index + 1 < len(headings) else len(lines)
        result.append(
            Section(
                title,
                level,
                start,
                max(start, end),
                len(re.findall(r"\b\w+\b", "\n".join(lines[start - 1 : end]))),
            )
        )
    return result


def links(text: str, body_start: int = 1) -> list[Link]:
    """Read inline and reference links; unresolved reference labels stay findings."""
    visible = prose_lines(text, body_start)
    definitions = {}
    result = []
    for _, line in visible:
        match = re.match(r"^ {0,3}\[([^]]+)\]:\s*(<[^>]+>|\S+)", line)
        if match:
            definitions[match[1].casefold()] = match[2].strip("<>")
    for number, line in visible:
        if re.match(r"^ {0,3}\[[^]]+\]:", line):
            continue
        for match in re.finditer(
            r"!?\[([^]\n]+)\](?:\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)|\[([^]]*)\])?",
            line,
        ):
            target = _reference_target(match.groups(), definitions)
            if target is not None:
                result.append(Link(target, number))
    return result


def _reference_target(
    groups: tuple[str, ...], definitions: dict[str, str]
) -> str | None:
    label, target, reference = groups
    if target is not None:
        return target.strip("<>")
    if reference is not None:
        return definitions.get(
            (reference or label).casefold(), "missing-reference:" + (reference or label)
        )
    return definitions.get(label.casefold())


def anchors(text: str) -> set[str]:
    """Compute GitHub-style heading anchors, including repeated-heading suffixes."""
    result = set(re.findall(r'<(?:a|h[1-6])\b[^>]*(?:id|name)=["\']([^"\']+)', text))
    counts: dict[str, int] = {}
    for section in sections(text):
        if section.level == 0:
            continue
        plain = re.sub(r"<[^>]+>", "", section.heading).lower()
        slug = "".join(
            c
            for c in plain
            if c in {"_", "-", " "} or unicodedata.category(c)[0] in {"L", "N"}
        ).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.add(f"{slug}-{count}" if count else slug)
    return result


def redirect(text: str, body_start: int = 1) -> Link | None:
    """Only a moved stub or a page made solely of one link forms a redirect."""
    found = links(text, body_start)
    if len(found) != 1:
        return None
    prose = "\n".join(
        line
        for _, line in prose_lines(text, body_start)
        if line.strip() and not line.startswith("#")
    )
    if re.match(r"(?i)^(?:\*\*)?Moved\b", prose) or re.fullmatch(
        r"\[[^]]+\]\([^)]+\)\.?", prose
    ):
        return found[0]
    return None
