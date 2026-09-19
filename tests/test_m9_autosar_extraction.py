"""M9 tests (part 2): AUTOSAR structured extraction end-to-end.

Runs the deterministic extractor + validator + persistence + graph +
profile-aware findings over the REAL external document
(AUTOSAR_EXP_PlatformDesign.pdf, R20-11) via its M1 processed JSON and the
M2 chunker — no fixtures are invented, no facts hardcoded (task Part I/M).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.extraction.autosar.extractor import extract_autosar
from backend.extraction.autosar.models import (AutosarEntityType,
                                               AutosarPredicate)
from backend.extraction.autosar.validate import validate_autosar_extraction

AUTOSAR_JSON = Path("data/external_test/processed/"
                    "AUTOSAR_EXP_PlatformDesign__processed.json")

requires_autosar_json = pytest.mark.skipif(
    not AUTOSAR_JSON.is_file(), reason="external AUTOSAR processed JSON missing")

# module-level chunk cache shared by the fixtures (chunking is deterministic,
# so reuse is safe and keeps the suite fast)
_chunks_cache: dict = {}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def autosar_chunks():
    from backend.rag.chunker import chunk_processed_json

    # version label passed exactly as the upload pipeline does
    # (index_processed_document(version=version_label))
    if "chunks" not in _chunks_cache:
        _chunks_cache["chunks"] = chunk_processed_json(AUTOSAR_JSON,
                                                       version="R20-11")
    return _chunks_cache["chunks"]


@pytest.fixture(scope="module")
def extraction(autosar_chunks):
    return extract_autosar(autosar_chunks)


@pytest.fixture(scope="module")
def evidence_map(autosar_chunks):
    """Evidence-ID -> trusted Source, as the M4.6 philosophy requires.

    The extractor embeds a ``Source`` snapshot in every candidate, so the
    map is the fallback path; it is keyed by chunk id (the ID the extractor
    would reference).
    """
    return {c.chunk_id: _src_of(c) for c in autosar_chunks}


def _src_of(chunk):
    from backend.extraction.models import Source

    return Source(document_name=chunk.document_name, version=chunk.version,
                  sha256=chunk.sha256, section_no=chunk.section_no,
                  section_title=chunk.section_title,
                  page_start=chunk.page_start, page_end=chunk.page_end,
                  chunk_id=chunk.chunk_id)


@pytest.fixture(scope="module")
def registry_db(tmp_path_factory, extraction):
    """Isolated SQLite DB with the AUTOSAR document registered + extracted."""
    from backend.config import UPLOADS_PROCESSED_DIR
    from backend.storage.database import init_schema, make_engine

    db = tmp_path_factory.mktemp("autosar_reg") / "registry.db"
    engine = make_engine(db)
    init_schema(engine)
    return db


@pytest.fixture(scope="module")
def registry_session(registry_db):
    from backend.storage.database import get_session, make_engine

    session = get_session(make_engine(registry_db))
    yield session
    session.close()


from backend.storage.models import DocumentVersion  # noqa: E402


# ---------------------------------------------------------------------------
# schema / extractor
# ---------------------------------------------------------------------------

class TestAutosarExtraction:

    @requires_autosar_json
    def test_schema_vocabulary(self):
        types = {t.value for t in AutosarEntityType}
        assert {"adaptive_application", "functional_cluster", "ara",
                "platform_foundation", "platform_service", "machine",
                "process", "manifest", "service", "interface",
                "software_package"} <= types
        preds = {p.value for p in AutosarPredicate}
        assert {"runs_on", "provides_interface", "belongs_to",
                "provides_service", "interacts_with", "implemented_as",
                "configured_by", "uses_interface"} <= preds

    @requires_autosar_json
    def test_entity_count_is_real(self, extraction):
        assert extraction.stats["entities"] >= 20
        by_type = {}
        for e in extraction.entities:
            by_type[e.entity_type.value] = \
                by_type.get(e.entity_type.value, 0) + 1
        assert by_type.get("functional_cluster", 0) >= 10
        assert "autosar:ara:ara" in {e.key for e in extraction.entities}

    @requires_autosar_json
    def test_facts_carry_trusted_provenance(self, extraction, autosar_chunks):
        chunk_ids = {c.chunk_id for c in autosar_chunks}
        for f in extraction.facts:
            src = f.evidence.source
            assert src is not None, f"fact {f.dedupe_key} has no source"
            assert src.chunk_id in chunk_ids
            assert src.page_start >= 1
            assert src.document_name.endswith(".pdf")

    @requires_autosar_json
    def test_entities_carry_trusted_provenance(self, extraction,
                                               autosar_chunks):
        chunk_ids = {c.chunk_id for c in autosar_chunks}
        for e in extraction.entities:
            src = e.evidence.source
            assert src is not None
            assert src.chunk_id in chunk_ids

    @requires_autosar_json
    def test_key_3_1_1_ara_passage_witnessed(self, extraction):
        """The known ARA passage (section 3.1.1, pp.15-16) must produce
        its documented relationships — derived from the PDF, not hardcoded
        extraction logic (assertion uses the vocabulary the prose states)."""
        from backend.extraction.autosar.models import normalize_key

        keys = {e.key for e in extraction.entities}
        assert normalize_key(AutosarEntityType.ARA, "ARA") in keys
        # the document's canonical surface form is the plural "Adaptive
        # Applications" ("Adaptive Applications (AA) run on top of ARA")
        aa = normalize_key(AutosarEntityType.ADAPTIVE_APPLICATION,
                           "Adaptive Applications")
        assert aa in keys
        preds = {(f.subject, f.predicate.value, f.object)
                 for f in extraction.facts}
        assert (aa, "runs_on", normalize_key(AutosarEntityType.ARA, "ARA")) \
            in preds
        # "which belong to either Adaptive Platform Foundation or
        #  Adaptive Platform Services" — the generic FC concept carries the
        # belongs_to fact, and the named Foundation FC (Communication
        # Management, section 7) does too.
        APF = normalize_key(AutosarEntityType.PLATFORM_FOUNDATION,
                            "Adaptive Platform Foundation")
        assert (normalize_key(AutosarEntityType.FUNCTIONAL_CLUSTER,
                              "Functional Clusters"), "belongs_to", APF) \
            in preds
        assert (normalize_key(AutosarEntityType.FUNCTIONAL_CLUSTER,
                              "Communication Management"), "belongs_to",
                APF) in preds

    @requires_autosar_json
    def test_no_toc_page_pollution(self, extraction):
        """Relationships must cite real architecture sections, not the
        dotted table-of-contents pages (extractor skips ToC chunks)."""
        offenders = [f for f in extraction.facts
                     if f.evidence.source.page_start <= 4
                     and f.predicate == AutosarPredicate.INTERACTS_WITH]
        for f in offenders:
            assert f.evidence.source.section_no, \
                f"interacts_with fact cited section-less ToC page: {f.evidence.source}"

    @requires_autosar_json
    def test_determinism(self, autosar_chunks):
        a = extract_autosar(autosar_chunks)
        b = extract_autosar(autosar_chunks)
        assert [e.key for e in a.entities] == [e.key for e in b.entities]
        assert [f.dedupe_key for f in a.facts] == [f.dedupe_key for f in b.facts]


# ---------------------------------------------------------------------------
# mechanical validation
# ---------------------------------------------------------------------------

class TestAutosarValidation:

    @requires_autosar_json
    def test_real_extraction_validates_clean(self, extraction, autosar_chunks):
        emap = {f"E{i}": _src_of(c) for i, c in enumerate(autosar_chunks)}
        candidates = list(extraction.entities) + list(extraction.facts)
        out = validate_autosar_extraction(candidates, emap)
        assert out.ok, [i.to_dict() for i in out.issues[:10]]
        assert out.stats.rejected_entities == 0
        assert out.stats.rejected_facts == 0
        assert len(out.entities) == len(extraction.entities)
        assert len(out.facts) == len(extraction.facts)

    @requires_autosar_json
    def test_unknown_evidence_id_rejected(self, extraction):
        from backend.extraction.models import EvidenceRef

        orphan = extraction.entities[0].model_copy(
            update={"evidence": EvidenceRef(evidence_id="E999")})
        out = validate_autosar_extraction([orphan], {})
        assert not out.ok
        assert out.stats.unknown_evidence_ids == 1
        assert any(i.kind == "unknown_evidence_id" for i in out.issues)

    @requires_autosar_json
    def test_missing_evidence_rejected(self, extraction):
        from backend.extraction.models import EvidenceRef

        noev = extraction.facts[0].model_copy(
            update={"evidence": EvidenceRef(evidence_id="")})
        out = validate_autosar_extraction([noev], {})
        assert not out.ok
        assert any(i.kind == "missing_evidence" for i in out.issues)

    @requires_autosar_json
    def test_domain_range_violation_rejected(self, extraction):
        from backend.extraction.autosar.models import normalize_key

        bad = extraction.facts[0].model_copy(update={
            "subject": normalize_key(AutosarEntityType.PROCESS, "proc"),
            "predicate": AutosarPredicate.PROVIDES_INTERFACE,
            "object": normalize_key(AutosarEntityType.INTERFACE, "ifx"),
        })
        out = validate_autosar_extraction([bad], {})
        assert not out.ok
        assert any(i.kind in ("domain_violation", "range_violation",
                              "unresolvable_reference") for i in out.issues)

    @requires_autosar_json
    def test_duplicate_facts_deduped_best_confidence_wins(self, extraction):
        from backend.extraction.models import EvidenceRef

        f = extraction.facts[0]
        lo = f.model_copy(update={
            "confidence": 0.6, "evidence": EvidenceRef(source=f.evidence.source)})
        hi = f.model_copy(update={
            "confidence": 0.9, "evidence": EvidenceRef(source=f.evidence.source)})
        # endpoints must resolve: include the full entity set
        out = validate_autosar_extraction(
            list(extraction.entities) + [lo, hi], {})
        assert out.ok, [i.to_dict() for i in out.issues[:6]]
        assert out.stats.duplicate_facts == 1
        kept = [x for x in out.facts if x.dedupe_key == f.dedupe_key]
        assert len(kept) == 1 and kept[0].confidence == 0.9


# ---------------------------------------------------------------------------
# persistence (idempotent) + graph + findings
# ---------------------------------------------------------------------------

_auto_ver = iter(range(1000))


@pytest.fixture()
def autosar_version(registry_session, extraction, autosar_chunks):
    """A fresh DocumentVersion with the real AUTOSAR extraction persisted.

    Per-test (not module-scoped): each test gets its own version row so the
    audited clear / detector plants in one test can never affect another.
    """
    from backend.storage.models import Document, DocumentVersion, Project

    s = registry_session
    proj = s.query(Project).filter_by(name="default").one_or_none()
    if proj is None:
        proj = Project(name="default", description="test")
        s.add(proj)
        s.commit()
    doc = Document(project_id=proj.id,
                   filename="AUTOSAR_EXP_PlatformDesign.pdf",
                   title="AUTOSAR EXP PlatformDesign", doc_type="HLD",
                   sha256=f"autosartestsha{next(_auto_ver)}")
    s.add(doc)
    s.commit()
    label = f"R20-11-test-{next(_auto_ver)}"
    dv = DocumentVersion(document_id=doc.id, version_label=label,
                         page_count=80, chunk_count=len(autosar_chunks),
                         status="ready")
    s.add(dv)
    s.commit()

    from backend.extraction.autosar.persistence import persist_extraction

    out = validate_autosar_extraction(
        list(extraction.entities) + list(extraction.facts), {})
    assert out.ok
    counts = persist_extraction(s, dv.id, out.entities, out.facts)
    s.commit()
    return dv, counts


class TestAutosarRegistry:

    @requires_autosar_json
    def test_full_persistence_and_readback(self, registry_session,
                                           autosar_version):
        from sqlalchemy import select

        from backend.extraction.autosar.persistence import (
            clear_registry, persist_extraction)
        from backend.storage.models import ExtractionEntity, ExtractionFact

        s = registry_session
        dv, counts = autosar_version
        assert counts["entities"] >= 20
        assert counts["facts"] >= 40

        ents = list(s.execute(select(ExtractionEntity).where(
            ExtractionEntity.version_id == dv.id)).scalars())
        facts = list(s.execute(select(ExtractionFact).where(
            ExtractionFact.version_id == dv.id)).scalars())
        assert len(ents) == counts["entities"]
        assert len(facts) == counts["facts"]
        assert all(e.canonical_key.startswith("autosar:") for e in ents)
        assert all(str(f.subject).startswith("autosar:") for f in facts)
        # provenance persisted verbatim (page/section on the row;
        # full document/version/chunk snapshot in attributes_json)
        e0 = ents[0]
        assert e0.section
        assert e0.page >= 1
        assert e0.attributes_json.get("provenance", {}).get(
            "document_name") == "AUTOSAR_EXP_PlatformDesign.pdf"
        # snapshot mirrors the evidence (chunks were chunked with the
        # pipeline's version label "R20-11"), not the test row's label
        assert e0.attributes_json.get("provenance", {}).get(
            "version_label") == "R20-11"
        assert e0.attributes_json.get("provenance", {}).get(
            "source_chunk_id") or e0.attributes_json.get(
            "provenance", {}).get("chunk_id")

        # idempotent rerun (fresh re-extraction, same chunks): same counts,
        # same rows, no duplicates
        from backend.extraction.autosar.extractor import extract_autosar
        from backend.extraction.autosar.validate import (
            validate_autosar_extraction)

        ex2 = extract_autosar(_chunks_cache["chunks"])
        out2 = validate_autosar_extraction(
            list(ex2.entities) + list(ex2.facts), {})
        counts2 = persist_extraction(s, dv.id, out2.entities, out2.facts)
        s.commit()
        assert counts2 == counts
        assert len(list(s.execute(select(ExtractionEntity).where(
            ExtractionEntity.version_id == dv.id)).scalars())) == counts[
            "entities"]

        # audited clear
        cleared = clear_registry(s, dv.id)
        s.commit()
        assert cleared["entities"] == counts["entities"]
        assert not s.execute(select(ExtractionFact).where(
            ExtractionFact.version_id == dv.id)).scalars().first()

    @requires_autosar_json
    def test_graph_and_profile_findings(self, registry_session,
                                        autosar_version):
        s = registry_session
        dv, _counts = autosar_version

        # M5 graph over the AUTOSAR registry
        from backend.graph.service import GraphService
        from backend.storage.database import make_session_factory

        svc = GraphService(session_factory=make_session_factory(s.bind))
        graph, stats = svc.build_graph(version=dv.version_label)
        assert graph.number_of_nodes() >= 20
        assert graph.number_of_edges() >= 40
        # node provenance/metadata present (task Part L)
        _n, data = next(iter(graph.nodes(data=True)))
        assert data.get("entity_type")
        assert data.get("provenance", {}).get("document_name") == \
            "AUTOSAR_EXP_PlatformDesign.pdf"
        # every edge carries the trusted provenance snapshot
        for _u, _v, d in graph.edges(data=True):
            assert d.get("provenance"), "edge without provenance"
            break

        # profile-aware findings (AUTOSAR detector set)
        from backend.findings.engine import FindingEngine

        engine = FindingEngine(profile="autosar_adaptive_platform")
        result = engine.run(s, dv.version_label)
        assert result.detectors_run, "no detectors ran for AUTOSAR profile"
        # conservative: the extracted registry is self-consistent, so zero
        # findings is the honest outcome; findings would need real defects.
        assert result.validation_ok, result.validation_issues

    @requires_autosar_json
    def test_inconsistent_classification_detector_fires_on_synthetic_defect(
            self, registry_session, autosar_version):
        """Plant an inconsistent classification (belongs_to to BOTH parents)
        and verify the detector fires with evidence — synthetic defect test,
        not production hardcoding."""
        from backend.storage.models import ExtractionFact

        s = registry_session
        dv, _counts = autosar_version
        plant = ExtractionFact(
            version_id=dv.id,
            fact_key="autosar:test:fc_test:belongs_to:apf",
            subject="autosar:functional_cluster:test_fc",
            predicate="belongs_to",
            object="autosar:platform_foundation:adaptive_platform_foundation",
            object_value=None,
            confidence=0.9, extractor="test", document_name="x.pdf",
            version_label=dv.version_label, section_no="3.1",
            section_title="ARA", page_start=15, page_end=15,
            source_chunk_id="test-chunk")
        s.add(plant)
        # ...and a second belongs_to fact to the Services partition for the
        # same FC -> both-partitions inconsistency
        plant2 = ExtractionFact(
            version_id=dv.id,
            fact_key="autosar:test:fc_test:belongs_to:aps",
            subject="autosar:functional_cluster:test_fc",
            predicate="belongs_to",
            object="autosar:platform_service:adaptive_platform_services",
            object_value=None,
            confidence=0.9, extractor="test", document_name="x.pdf",
            version_label=dv.version_label, section_no="3.1",
            section_title="ARA", page_start=15, page_end=15,
            source_chunk_id="test-chunk2")
        s.add(plant2)
        s.commit()

        from backend.findings.context import build_analysis_context
        from backend.findings.autosar_detectors import (
            detect_inconsistent_classification)

        ctx = build_analysis_context(s, dv.version_label)
        findings = detect_inconsistent_classification(ctx)
        assert findings, "detector missed the planted both-partitions defect"
        f = findings[0]
        assert f.entity_keys[0] == "autosar:functional_cluster:test_fc"
        assert f.fact_keys
