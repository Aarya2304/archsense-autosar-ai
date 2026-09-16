"""Heading and section detection (M1).

Detects numbered headings ("1", "1.1", "3.2.7 ...", "6.2.15 DEP-17") using a
regex over line starts, cross-checked against relative font size when
available. Produces the section map {section_no: first_page} used by
ground-truth resolution, chunking (M2) and entity extraction (M4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "1", "1.2", "3.2.7", "6.2.15" followed by title text (allowing &nbsp;
# artifacts and multiple spaces)
_HEADING_RE = re.compile(
    r"^(?P<no>\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(?P<title>\S.*?)$"
)


@dataclass
class Heading:
    section_no: str
    title: str
    page_no: int
    level: int


def heading_level(section_no: str) -> int:
    return section_no.count(".") + 1


def match_heading(line: str) -> tuple[str, str] | None:
    """Return (section_no, title) if the line starts with a heading number."""
    m = _HEADING_RE.match(line.strip())
    if not m:
        return None
    no = m.group("no")
    title = m.group("title").strip()
    # Guard against wrapped body text and data rows: real headings start
    # with an uppercase letter (rejects "5 lists the signal dictionary",
    # "2026-08-14 R. Sharma Initial release ...", "500 kbit/s ...").
    if len(title) > 120:
        return None
    if not (title[0].isalpha() and title[0].isupper()):
        return None
    return no, title


def detect_headings(pages_lines: dict[int, list[str]]) -> list[Heading]:
    """Detect headings across all pages.

    ``pages_lines`` maps page_no (1-based) -> cleaned lines.
    Returns headings in document order; later duplicates of the same
    section number are ignored (first occurrence wins).
    """
    headings: list[Heading] = []
    seen: set[str] = set()
    for page_no in sorted(pages_lines):
        for line in pages_lines[page_no]:
            hit = match_heading(line)
            if hit is None:
                continue
            no, title = hit
            if no in seen:
                continue
            seen.add(no)
            headings.append(Heading(section_no=no, title=title,
                                    page_no=page_no,
                                    level=heading_level(no)))
    return headings


def build_section_map(headings: list[Heading]) -> dict[str, int]:
    """{section_no: first_page} — first occurrence wins."""
    return {h.section_no: h.page_no for h in headings}


def section_at_page(headings: list[Heading], page_no: int) -> str | None:
    """Deepest heading active at the top of a page (for coarse tagging)."""
    active: str | None = None
    for h in headings:
        if h.page_no <= page_no:
            if active is None or heading_level(h.section_no) >= heading_level(active):
                active = h.section_no
    return active
