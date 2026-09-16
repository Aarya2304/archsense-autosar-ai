"""M1 tests: page-aware ingestion on the synthetic corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import SAMPLE_DOCS_DIR
from backend.ingestion.cleaning import (assemble_page_text, clean_lines,
                                        looks_like_banner, looks_like_pagenum)
from backend.ingestion.models import PageRecord
from backend.ingestion.pipeline import ingest_pdf
from backend.ingestion.sections import detect_headings, match_heading
from backend.ingestion.table_extractor import linearize_table


@pytest.fixture(scope="module")
def ingested_v1():
    return ingest_pdf(SAMPLE_DOCS_DIR / "ABC_HLD_v1.0.0.pdf")


@pytest.fixture(scope="module")
def ingested_v2():
    return ingest_pdf(SAMPLE_DOCS_DIR / "ABC_HLD_v1.1.0.pdf")


# ------------------------------------------------------------ page facts ----


def test_page_counts(ingested_v1, ingested_v2):
    assert ingested_v1.page_count >= 15
    assert ingested_v2.page_count >= 15


def test_sha256_stable(ingested_v1):
    import hashlib
    h = hashlib.sha256(
        (SAMPLE_DOCS_DIR / ingested_v1.document_name).read_bytes()).hexdigest()
    assert ingested_v1.sha256 == h


def test_page_records_complete(ingested_v1):
    assert [p.page_no for p in ingested_v1.pages] == list(
        range(1, ingested_v1.page_count + 1))
    assert all(p.char_count > 0 for p in ingested_v1.pages[1:])


def test_ocr_not_needed_for_digital_docs(ingested_v1, ingested_v2):
    assert all(not p.ocr_used for p in ingested_v1.pages)
    assert all(not p.ocr_used for p in ingested_v2.pages)


# ---------------------------------------------------------- section maps ----


def test_section_map_matches_ground_truth(ingested_v1, ingested_v2, dataset):
    for ingested, tag in ((ingested_v1, "v1"), (ingested_v2, "v2")):
        gt = dataset["page_index"][tag]
        for sec, page in gt.items():
            if sec == "0":  # title-page pseudo-section: not a numbered heading
                continue
            assert ingested.sections.get(sec) == page, (
                tag, sec, page, ingested.sections.get(sec))


def test_heading_matcher_rejects_prose():
    assert match_heading("5 lists the signal dictionary for each interface") is None
    assert match_heading("2026-08-14 R. Sharma Initial release of the ABC") is None
    assert match_heading("500 kbit/s operates the CAN bus") is None


def test_heading_matcher_accepts_headings():
    assert match_heading("1 Introduction") == ("1", "Introduction")
    assert match_heading("3.2.7  Component C-07: KeylessEntrySWC") == (
        "3.2.7", "Component C-07: KeylessEntrySWC")
    assert match_heading("6.2.15  DEP-15") == ("6.2.15", "DEP-15")


def test_detect_headings_dedupes(ingested_v1):
    heads = detect_headings({p.page_no: p.lines for p in ingested_v1.pages})
    nos = [h.section_no for h in heads]
    assert len(nos) == len(set(nos))


# ------------------------------------------------------------- cleaning ----


def test_cleaning_drops_pagenum_and_banner():
    lines = ["1 Introduction", "Page 3", "SYNTHETIC SAMPLE — NOT A REAL PROGRAM DOCUMENT"]
    cleaned, dropped = clean_lines(lines)
    assert cleaned == ["1 Introduction"]
    assert dropped == 2


def test_looks_like_pagenum():
    assert looks_like_pagenum("Page 12")
    assert looks_like_pagenum("12")
    assert not looks_like_pagenum("Version 12")


def test_assemble_text_joins_hyphenated_breaks():
    assert "information" == assemble_page_text(["informa-", "tion"]).strip()


def test_banner_detection():
    assert looks_like_banner("SYNTHETIC SAMPLE — NOT A REAL PROGRAM DOCUMENT")
    assert looks_like_banner("ABC-HLD-SYN | v1.0.0")
    assert not looks_like_banner("DoorControlSWC requires door lock commands.")


# --------------------------------------------------------------- tables ----


def test_tables_extracted(ingested_v1):
    assert len(ingested_v1.tables) >= 40
    assert all(t.n_rows >= 1 for t in ingested_v1.tables)
    assert all(t.n_cols >= 2 for t in ingested_v1.tables)


def test_signal_table_content(ingested_v1):
    """The Signal Dictionary table must surface with expected rows."""
    sig_tables = [t for t in ingested_v1.tables
                  if "Signal ID" in t.headers]
    assert sig_tables, "signal dictionary table not found"
    t = sig_tables[0]
    rows_as_text = " | ".join(" ".join(r) for r in t.rows)
    assert "SG-001" in rows_as_text
    assert "VehicleSpeed" in rows_as_text


def test_page_table_anchors(ingested_v1):
    for t in ingested_v1.tables:
        assert 1 <= t.page_no <= ingested_v1.page_count


def test_linearize_table():
    txt = linearize_table(["A", "B"], [["1", "2"]])
    assert "A | B" in txt and "1 | 2" in txt


# ------------------------------------------------------------ page text ----


def test_page_text_contains_component_prose(ingested_v1):
    """Section 3.2.8 page (BodyControlSWC detail) must contain its prose."""
    gt = ingest_pdf(SAMPLE_DOCS_DIR / "ABC_HLD_v1.0.0.pdf").sections
    page = gt["3.2.8"]
    rec = ingested_v1.pages[page - 1]
    assert "BodyControlSWC" in rec.text
    assert "arbiter" in rec.text


def test_metadata_present(ingested_v1):
    meta = ingested_v1.metadata
    assert meta["file_name"] == "ABC_HLD_v1.0.0.pdf"
    assert meta["page_count"] == ingested_v1.page_count
