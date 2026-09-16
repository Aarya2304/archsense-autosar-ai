"""Line-level cleaning for ingested pages (M1).

Removes running headers/footers, page-number lines and the synthetic
document's security banner so they don't pollute chunks (M2) or confuse
section detection. All rules are conservative: a line must *match* a known
noise pattern to be dropped, and we never drop lines that look like real
content.
"""

from __future__ import annotations

import re

# Lines that are exactly a page number, optionally with decorations
_PAGENUM_RE = re.compile(r"^\s*(?:Page\s+)?\d{1,3}\s*$", re.IGNORECASE)
# Composite footer: "SYNTHETIC SAMPLE — ..." / organization banner lines
_BANNER_PATTERNS = [
    re.compile(r"^SYNTHETIC SAMPLE\b", re.IGNORECASE),
    re.compile(r"^Synthetic Demonstration Program", re.IGNORECASE),
    re.compile(r"^ABC-HLD-SYN\b"),
    re.compile(r"^Adaptive Body Controller \(ABC\) — High-Level Design$"),
    re.compile(r"^Body & Comfort Domain — AUTOSAR Classic Platform$"),
]
_HYPHEN_RE = re.compile(r"(\w)-\n")


def looks_like_pagenum(line: str) -> bool:
    return bool(_PAGENUM_RE.match(line))


def looks_like_banner(line: str) -> bool:
    return any(p.match(line) for p in _BANNER_PATTERNS)


def clean_lines(lines: list[str]) -> tuple[list[str], int]:
    """
    Clean one page's lines.

    Returns (cleaned_lines, n_dropped). Order-preserving; hyphenated
    line-break joins are performed at the text-assembly stage (see
    ``assemble_page_text``), not here.
    """
    cleaned: list[str] = []
    dropped = 0
    for ln in lines:
        if looks_like_pagenum(ln) or looks_like_banner(ln):
            dropped += 1
            continue
        cleaned.append(ln)
    return cleaned, dropped


def assemble_page_text(lines: list[str]) -> str:
    """Join cleaned lines into page text, re-joining hyphenated breaks."""
    text = "\n".join(lines)
    # join "informa-\ntion" -> "information" (only when break is mid-word)
    while True:
        m = _HYPHEN_RE.search(text)
        if not m:
            break
        text = text[:m.start()] + m.group(1) + text[m.end():]
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
