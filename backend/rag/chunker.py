"""Deterministic section-aware chunking (M2.1).

Chunking strategy (documented in docs/PROJECT_DECISIONS.md D-011):

- Input is the M1 ``IngestionResult`` JSON (D-008): per-page cleaned lines,
  per-page starting sections, table records. The chunker never re-parses
  PDFs.
- Content is first re-grouped into per-section line streams by walking
  pages in order and tracking the active numbered section; heading lines
  become the first line of their own section stream (so a chunk always
  contains its own heading).
- Table cells embedded in the page lines are dropped (a deterministic
  fragment filter recognises them) and each extracted table is re-emitted
  as a standalone ``table`` chunk in deterministic linearized form
  (``linearize_table``), keeping tables coherent for retrieval.
- Prose is split on paragraph boundaries, then by token budget:
  target ~500 tokens (4 chars/token heuristic) with ~15% overlap on
  continuation chunks. No arbitrary mid-sentence hard splits except for
  degenerate over-long paragraphs.
- Small sections are kept whole (never split below one paragraph).
- Output is ordered by (version, chunk_seq) and chunk IDs are deterministic
  (sha1 over document identity + section + seq + text hash), so repeated
  runs over identical input produce identical IDs (upsert-friendly).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from backend.ingestion.table_extractor import linearize_table
from backend.rag.models import Chunk

_CHARS_PER_TOKEN = 4  # deterministic heuristic token estimate

# Line fragments that are (parts of) table cells rather than prose.
# Table content is re-emitted from TableRecords, so these lines are
# dropped from prose streams. Kept deliberately narrow to avoid eating
# real prose.
_CELL_LINE_RE = re.compile(
    r"^(?:[CS]-\d{2}(?:\s*\([^)]*\))??"        # C-01 / C-01 (DoorControlSWC)
    r"|IF-\d{2}"
    r"|SG-\d{3}"
    r"|DEP-\d{2}"
    r"|FL-\d"
    r"|P-\d{3}"
    r"|uint\d+|boolean|float\d*|string(?:\[\d+\])?"
    r"|(?:km/h|%|mA|ms|°C|-))"
    r"(?:\s*\|\s*.*)?$"
)
_SIGNAL_ROW_RE = re.compile(r"^(?:[CS]-\d{2}\s+\S+|SG-\d{3}\s+\S+)$")


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate (~4 chars/token)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def make_chunk_id(version: str, section_no: str, chunk_seq: int,
                  text: str, doc_sha: str) -> str:
    """Deterministic chunk ID: sha1[:16] over identity + content hash.

    Includes the content hash so edited documents get new IDs (fresh
    embeddings) while unchanged content keeps its ID (upsert = no dupes).
    """
    text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    identity = f"{doc_sha[:12]}|{version}|{section_no}|{chunk_seq:04d}|{text_hash}"
    return hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]


def _table_line_fragments(tables: list[dict]) -> set[str]:
    """Lines that are table-cell fragments, derived from the tables themselves."""
    frags: set[str] = set()
    for t in tables:
        for h in t.get("headers", []):
            if h:
                frags.add(h)
        for row in t.get("rows", []):
            for cell in row:
                if cell:
                    frags.add(cell)
    return frags


def _is_cell_fragment(line: str, fragments: set[str]) -> bool:
    """True when a line is (part of) a table rather than prose.

    Uses three conservative signals: exact cell match, known ID-pattern
    prefix rows, or short enum/datatype-like lines.
    """
    s = line.strip()
    if not s:
        return True
    if s in fragments:
        return True
    if _CELL_LINE_RE.match(s):
        return True
    if _SIGNAL_ROW_RE.match(s):
        return True
    # Short lines ending with a unit or a lone dash (table cell columns)
    if len(s) <= 24 and (s in {"-", "—"} or s.endswith((" kbit/s", " ms", " mA"))):
        return True
    return False


def _group_sections(pages: list[dict], tables: list[dict]) -> dict[str, list]:
    """Re-group cleaned page lines into per-section line streams.

    Returns {section_no: [(page_no, line), ...]} where the first entry of
    each section is its heading line. Title-page/front-matter content
    (before section "1") is grouped under "".
    """
    fragments = _table_line_fragments(tables)
    streams: dict[str, list] = {}
    current = ""  # front matter (title page) until section "1" starts
    for page in sorted(pages, key=lambda p: p["page_no"]):
        page_no = page["page_no"]
        starts = set(page.get("sections", []))
        for line in page.get("lines", []):
            stripped = line.strip()
            if not stripped:
                continue
            m = re.match(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(\S.*)$", stripped)
            if m and m.group(1) in starts and m.group(1) != current:
                # A heading for a section that starts on this page.
                # Ambiguity: prose can look like headings; heading
                # detection already ran in M1 with conservative rules, so
                # trust the page's section-start list + number match.
                current = m.group(1)
                streams.setdefault(current, []).append((page_no, stripped))
                continue
            if current and _is_cell_fragment(stripped, fragments):
                continue  # table noise; tables re-emitted separately
            if not current and _is_cell_fragment(stripped, fragments):
                continue  # title-page table cells (Field/Value grid)
            streams.setdefault(current, []).append((page_no, stripped))
    return streams


def _split_paragraphs(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Join wrapped lines into paragraph blocks (blank-ish separators).

    The M1 cleaner drops empty lines, so paragraph boundaries are
    approximated by sentence-final short lines and heading lines.
    Returns [(first_page_no, paragraph_text), ...].
    """
    paragraphs: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_page = 0
    for page_no, line in lines:
        buf.append(line)
        if buf_page == 0:
            buf_page = page_no
        # A line that ends a sentence AND is short-ish, or a heading-like
        # line, ends the current paragraph block.
        if (len(line) < 80 and line.rstrip().endswith((".", ":", "!", "?"))
                and not line.endswith(("e.g.", "i.e.", "etc."))):
            paragraphs.append((buf_page, " ".join(buf)))
            buf, buf_page = [], 0
    if buf:
        paragraphs.append((buf_page, " ".join(buf)))
    return paragraphs


def _pack_paragraphs(packed: list[dict], target_tokens: int,
                     overlap_tokens: int) -> list[dict]:
    """Pack paragraph blocks (from _split_paragraphs/_split_large_paragraph)
    into chunks under the token budget.

    Input/output items are {"text", "page_start", "page_end"}. Overlap
    repeats the tail blocks of the previous chunk (provenance pages are
    the union).
    """
    chunks: list[dict] = []
    cur: list[dict] = []
    cur_tokens = 0
    for block in packed:
        t = estimate_tokens(block["text"])
        if cur and cur_tokens + t > target_tokens:
            chunks.append({
                "text": " ".join(x["text"] for x in cur),
                "page_start": min(x["page_start"] for x in cur),
                "page_end": max(x["page_end"] for x in cur),
            })
            # overlap: keep tail blocks worth ~overlap_tokens
            tail: list[dict] = []
            tail_tokens = 0
            for blk in reversed(cur):
                tt = estimate_tokens(blk["text"])
                if tail and tail_tokens + tt > overlap_tokens:
                    break
                tail.insert(0, blk)
                tail_tokens += tt
            cur = tail
            cur_tokens = tail_tokens
        cur.append(block)
        cur_tokens += t
    if cur:
        chunks.append({
            "text": " ".join(x["text"] for x in cur),
            "page_start": min(x["page_start"] for x in cur),
            "page_end": max(x["page_end"] for x in cur),
        })
    return chunks


def _split_large_paragraph(text: str, page_no: int, target_tokens: int,
                           overlap_tokens: int) -> list[dict]:
    """Fallback for a single paragraph exceeding the budget: sentence pack."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[dict] = []
    cur: list[str] = []
    cur_tokens = 0
    for s in sentences:
        t = estimate_tokens(s)
        if cur and cur_tokens + t > target_tokens:
            chunks.append({"text": " ".join(cur), "page_start": page_no,
                           "page_end": page_no})
            tail = " ".join(cur)[-overlap_tokens * _CHARS_PER_TOKEN:]
            cur = [tail] if tail.strip() else []
            cur_tokens = estimate_tokens(tail)
        cur.append(s)
        cur_tokens += t
    if cur:
        chunks.append({"text": " ".join(cur), "page_start": page_no,
                       "page_end": page_no})
    return chunks


def _section_title(first_line: str, section_no: str) -> str:
    m = re.match(rf"^{re.escape(section_no)}\.?\s+(\S.*)$", first_line)
    return (m.group(1).strip() if m else first_line.strip())[:120]


def chunk_ingestion_result(ingested: dict,
                           version: str | None = None,
                           target_tokens: int = 500,
                           overlap_tokens: int = 75) -> list[Chunk]:
    """Chunk one M1 ``IngestionResult`` dict into deterministic chunks.

    ``version`` defaults to a label derived from the document name
    (``ABC_HLD_v1.0.0`` -> ``1.0.0``).
    """
    if version is None:
        m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", ingested["document_name"])
        version = m.group(1) if m else "unversioned"

    sha = ingested["sha256"]
    doc_name = ingested["document_name"]
    streams = _group_sections(ingested["pages"], ingested["tables"])

    # Tables are emitted as standalone chunks; multiple tables on the same
    # page+index combination are kept in page order.
    table_records = sorted(ingested["tables"],
                           key=lambda t: (t["page_no"], t["table_index"]))

    chunks: list[Chunk] = []

    # --- prose chunks per section, in document order of section start ---
    section_order = sorted(streams.keys(),
                           key=lambda s: (streams[s][0][0], 0 if s else 1, s))
    for section_no in section_order:
        lines = streams[section_no]
        first_page, first_line = lines[0]
        title = _section_title(first_line, section_no) if section_no else ""
        paragraphs = _split_paragraphs(lines)
        packed: list[dict] = []
        for pg, text in paragraphs:
            t = estimate_tokens(text)
            if t > target_tokens * 1.5:
                packed.extend(_split_large_paragraph(
                    text, pg, target_tokens, overlap_tokens))
            else:
                packed.append({"text": text, "page_start": pg,
                               "page_end": pg})
        packed = _pack_paragraphs(packed, target_tokens, overlap_tokens)
        for i, pc in enumerate(packed):
            text = pc["text"]
            chunks.append(Chunk(
                chunk_id=make_chunk_id(version, section_no, i, text, sha),
                document_name=doc_name,
                version=version,
                sha256=sha,
                section_no=section_no,
                section_title=title,
                page_start=pc["page_start"],
                page_end=pc["page_end"],
                chunk_seq=0,          # renumbered globally below
                chunk_type="prose",
                text=text,
                token_count=estimate_tokens(text),
            ))

    # --- table chunks (standalone, coherent) ---
    table_sections = _assign_table_sections(ingested)
    table_chunks: list[Chunk] = []
    for t in table_records:
        text = linearize_table(t["headers"], t["rows"])
        if not text.strip():
            continue
        section_no = table_sections.get((t["page_no"], t["table_index"]), "")
        table_chunks.append(Chunk(
            chunk_id=make_chunk_id(version, section_no, 0, text, sha),
            document_name=doc_name,
            version=version,
            sha256=sha,
            section_no=section_no,
            section_title="",
            page_start=t["page_no"],
            page_end=t["page_no"],
            chunk_seq=0,
            chunk_type="table",
            text=text,
            token_count=estimate_tokens(text),
        ))

    # --- global ordering + sequence numbering ---
    # Interleave by first page so document order is preserved; tables on
    # page N sort after prose starting on page N (they belong to sections
    # that start on or before N).
    def sort_key(c: Chunk) -> tuple:
        return (c.page_start, 0 if c.chunk_type == "prose" else 1,
                c.section_no, c.chunk_seq)

    prose_by_id = {c.chunk_id: c for c in chunks}
    table_by_id = {c.chunk_id: c for c in table_chunks}
    merged = sorted(list(prose_by_id.values()) + list(table_by_id.values()),
                    key=sort_key)
    renumbered: list[Chunk] = []
    for seq, c in enumerate(merged):
        if c.chunk_type == "table":
            # disambiguate multiple tables sharing section+content-free seq
            cid = make_chunk_id(version, c.section_no, seq, c.text, sha)
        else:
            cid = c.chunk_id
        renumbered.append(Chunk(
            chunk_id=cid,
            document_name=c.document_name,
            version=c.version,
            sha256=c.sha256,
            section_no=c.section_no,
            section_title=c.section_title,
            page_start=c.page_start,
            page_end=c.page_end,
            chunk_seq=seq,
            chunk_type=c.chunk_type,
            text=c.text,
            token_count=c.token_count,
        ))
    return renumbered


def _sec_numeric_key(section_no: str) -> tuple[int, ...]:
    """Numeric sort key for a dotted section number ('' -> (0,))."""
    if not section_no:
        return (0,)
    try:
        return tuple(int(part) for part in section_no.split("."))
    except ValueError:
        return (0,)


def _deepest_section_at(section_map: dict[str, int], page_no: int) -> str:
    """Fallback: deepest section whose first page <= the table's page.

    Ties (several sections starting on the same page) are broken by numeric
    section order, highest last. Deterministic but coarser than the
    anchor-line assignment in ``_assign_table_sections``.
    """
    best, best_key = "", (0,)
    for sec, page in section_map.items():
        if 0 < page <= page_no:
            key = _sec_numeric_key(sec)
            if key > best_key:
                best, best_key = sec, key
    return best


def _table_anchor_candidates(table: dict) -> list[str]:
    """Lines likely marking the table's position in the page's line stream."""
    cands: list[str] = []
    headers = table.get("headers") or []
    rows = table.get("rows") or []
    if headers and headers[0]:
        cands.append(str(headers[0]).strip())
    if rows and rows[0] and rows[0][0]:
        cands.append(str(rows[0][0]).strip())
    seen: set[str] = set()
    return [c for c in cands if c and not (c in seen or seen.add(c))]


def _assign_table_sections(ingested: dict) -> dict[tuple[int, int], str]:
    """Deterministically assign each extracted table its owning section.

    Primary signal (D-012): locate the table's anchor line (first header
    cell, else first row's first cell) within its page's ordered lines and
    attribute the table to the numbered heading in effect at that point in
    reading order (heading positions come from the same conservative M1
    heading detection the prose grouping uses; the section in effect carries
    across pages). Fallback when no anchor line is found: the deepest
    section whose start page <= the table's page (numeric tie-break).

    Returns {(page_no, table_index): section_no}.
    """
    section_map = ingested["sections"]
    assignments: dict[tuple[int, int], str] = {}
    carried = ""  # section in effect at the end of the previous page
    for page in sorted(ingested["pages"], key=lambda p: p["page_no"]):
        page_no = page["page_no"]
        starts = set(page.get("sections", []))
        heading_idxs: list[tuple[int, str]] = []
        for idx, line in enumerate(page.get("lines", [])):
            m = re.match(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+\S", line.strip())
            if m and m.group(1) in starts and m.group(1) != carried:
                carried = m.group(1)
                heading_idxs.append((idx, carried))
        page_tables = sorted(
            (t for t in ingested["tables"] if t["page_no"] == page_no),
            key=lambda t: t["table_index"])
        for t in page_tables:
            anchor = None
            for cand in _table_anchor_candidates(t):
                for idx, line in enumerate(page.get("lines", [])):
                    if line.strip() == cand:
                        anchor = idx
                        break
                if anchor is not None:
                    break
            if anchor is None:
                assignments[(page_no, t["table_index"])] = \
                    _deepest_section_at(section_map, page_no)
                continue
            sec = carried if not heading_idxs or heading_idxs[0][0] > anchor \
                else ""
            for hidx, hsec in heading_idxs:
                if hidx <= anchor:
                    sec = hsec
                else:
                    break
            assignments[(page_no, t["table_index"])] = sec
    return assignments


def chunk_processed_json(path: Path, **kwargs) -> list[Chunk]:
    """Chunk a saved ``*__processed.json`` file."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return chunk_ingestion_result(payload, **kwargs)
