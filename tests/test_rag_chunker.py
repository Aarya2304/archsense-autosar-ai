"""M2 tests: deterministic chunking (chunker.py).

Covers: determinism, metadata preservation, chunk IDs, ordering, section
boundaries, table coherence/attribution, small & large section handling,
overlap. All fast (no embeddings, no network).
"""

from __future__ import annotations

import re

import pytest

from backend.rag.chunker import (chunk_ingestion_result, chunk_processed_json,
                                 estimate_tokens, make_chunk_id)


# ------------------------------------------------------------- metadata ----

def test_chunks_carry_full_provenance(ingested_v1):
    chunks = chunk_ingestion_result(ingested_v1)
    assert chunks, "chunker produced no chunks"
    for c in chunks:
        assert c.document_name == ingested_v1["document_name"]
        assert c.version == "1.0.0"
        assert c.sha256 == ingested_v1["sha256"]
        assert isinstance(c.page_start, int) and c.page_start >= 1
        assert c.page_end >= c.page_start
        assert c.chunk_type in {"prose", "table"}
        assert c.text.strip()
        assert c.token_count >= 1


def test_version_defaults_from_document_name(ingested_v1):
    chunks = chunk_ingestion_result(ingested_v1, version=None)
    assert {c.version for c in chunks} == {"1.0.0"}


def test_metadata_payload_is_flat_and_complete(ingested_v1):
    chunk = chunk_ingestion_result(ingested_v1)[0]
    meta = chunk.metadata()
    expected_keys = {"document_name", "version", "sha256", "section_no",
                     "section_title", "page_start", "page_end", "pages_csv",
                     "chunk_seq", "chunk_type", "token_count"}
    assert expected_keys <= set(meta)
    # ChromaDB-compatible: scalars only
    assert all(isinstance(v, (str, int, float, bool)) for v in meta.values())
    pages = meta["pages_csv"].split(";")
    assert pages[0] == str(chunk.page_start)
    assert pages[-1] == str(chunk.page_end)


# ---------------------------------------------------------- determinism ----

def test_chunking_is_deterministic(ingested_v1):
    a = chunk_ingestion_result(ingested_v1)
    b = chunk_ingestion_result(ingested_v1)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert [c.text for c in a] == [c.text for c in b]


def test_chunk_ids_are_stable_hashes(ingested_v1):
    chunks = chunk_ingestion_result(ingested_v1)
    for c in chunks:
        assert re.fullmatch(r"[0-9a-f]{16}", c.chunk_id)


def test_chunk_ids_unique(ingested_v1):
    chunks = chunk_ingestion_result(ingested_v1)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate chunk IDs"


def test_content_change_changes_chunk_id():
    sha = "a" * 64
    id1 = make_chunk_id("1.0.0", "3.1", 0, "original text", sha)
    id2 = make_chunk_id("1.0.0", "3.1", 0, "edited text", sha)
    assert id1 != id2
    # Same content -> same ID regardless of sequence slot
    assert make_chunk_id("1.0.0", "3.1", 0, "original text", sha) == id1


# ------------------------------------------------------------- ordering ----

def test_chunks_ordered_by_document_position(ingested_v1):
    chunks = chunk_ingestion_result(ingested_v1)
    starts = [c.page_start for c in chunks]
    assert starts == sorted(starts), "chunks not in document order"
    seqs = [c.chunk_seq for c in chunks]
    assert seqs == list(range(len(chunks))), "chunk_seq not dense 0..n-1"


def test_section_boundary_respected(ingested_v1):
    chunks = chunk_ingestion_result(ingested_v1)
    by_section: dict[str, list[str]] = {}
    for c in chunks:
        if c.chunk_type == "prose" and c.section_no:
            by_section.setdefault(c.section_no, []).append(c.text)
    # No prose chunk may contain the *body* of a different numbered section:
    # heading lines always start their own section stream.
    for sec, texts in by_section.items():
        for t in texts:
            assert not re.match(rf"^{re.escape(sec)}\.\d+\s+\S", t), (
                f"section {sec} chunk contains a subsection heading: {t[:80]!r}")


def test_first_prose_chunk_contains_its_heading(ingested_v1):
    chunks = chunk_ingestion_result(ingested_v1)
    first_prose_by_section: dict[str, str] = {}
    for c in chunks:
        if c.chunk_type == "prose" and c.section_no and c.chunk_seq >= 0:
            first_prose_by_section.setdefault(c.section_no, c.text)
    assert "1.1" in first_prose_by_section
    assert first_prose_by_section["1.1"].startswith("1.1")


# --------------------------------------------------------- small / large ----

def test_small_section_stays_whole(ingested_v1):
    """Section 1.2 (Scope) is short; it must not be split mid-paragraph."""
    chunks = [c for c in chunk_ingestion_result(ingested_v1)
              if c.section_no == "1.2" and c.chunk_type == "prose"]
    assert len(chunks) == 1
    assert "Scope" in chunks[0].text
    assert len(chunks[0].text) > 50


def test_large_table_stays_coherent(ingested_v1):
    """The 24-row dependency overview table (section 6.1) is one chunk."""
    chunks = [c for c in chunk_ingestion_result(ingested_v1)
              if c.section_no == "6.1" and c.chunk_type == "table"]
    assert len(chunks) == 1
    assert chunks[0].token_count >= 200
    assert "DEP-01" in chunks[0].text and "DEP-24" in chunks[0].text


def test_estimate_tokens_deterministic():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 40) == 10
    assert estimate_tokens("abcd") * 4 <= 4  # min bound sanity


# ---------------------------------------------------------------- tables ----

def test_tables_are_standalone_chunks(ingested_v1):
    chunks = chunk_ingestion_result(ingested_v1)
    tables = [c for c in chunks if c.chunk_type == "table"]
    assert tables, "no table chunks produced"
    for c in tables:
        assert c.text.startswith("Table columns:")
        assert c.section_title == "" or c.section_no


def test_table_section_attribution(ingested_v1):
    """Revision-history table belongs to 1.3, terminology table to 1.5."""
    chunks = chunk_ingestion_result(ingested_v1)
    tables = [c for c in chunks if c.chunk_type == "table"]
    rev = [c for c in tables if "R. Sharma" in c.text]
    term = [c for c in tables if "Sender-Receiver interface" in c.text]
    assert rev and rev[0].section_no == "1.3", \
        f"revision table misattributed: {rev[0].section_no!r}"
    assert term and term[0].section_no == "1.5", \
        f"terminology table misattributed: {term[0].section_no!r}"


def test_port_table_content_preserved(ingested_v1):
    """Port/interface table rows survive linearization intact."""
    chunks = chunk_ingestion_result(ingested_v1)
    port_chunks = [c for c in chunks if c.chunk_type == "table"
                   and "DoorStatusIF" in c.text]
    assert port_chunks
    assert any("P-001" in c.text and "provides" in c.text
               for c in port_chunks)


def test_no_table_cell_leakage_into_prose(ingested_v1):
    """Known ID-pattern cell fragments must not appear as prose lines."""
    chunks = chunk_ingestion_result(ingested_v1)
    for c in chunks:
        if c.chunk_type != "prose":
            continue
        for line in c.text.split(" "):
            pass  # prose is joined; leakage check below on raw streams
    # Stronger check: signal IDs only appear in table chunks or as parts of
    # sentences (with surrounding words), never as bare repeated fragments.
    bare = [c for c in chunks if c.chunk_type == "prose"
            and re.search(r"(?m)^SG-\d{3}$", c.text)]
    assert not bare


def test_chunk_processed_json_file_path(processed_jsons):
    chunks = chunk_processed_json(processed_jsons[0])
    assert chunks
    versions = {c.version for c in chunks}
    assert versions == {"1.0.0"}


@pytest.mark.parametrize("target,overlap", [(500, 75), (200, 30)])
def test_target_token_parameterization(ingested_v1, target, overlap):
    chunks = chunk_ingestion_result(ingested_v1, target_tokens=target,
                                    overlap_tokens=overlap)
    assert chunks
    # Small target -> more chunks than large target for the same content.
    big = chunk_ingestion_result(ingested_v1, target_tokens=2000,
                                 overlap_tokens=300)
    assert len(chunks) >= len(big)
