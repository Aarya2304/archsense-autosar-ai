"""M6 tests: engine orchestration, persistence (idempotency, review
preservation, version isolation), and ground-truth evaluation.

Persistence/engine tests run on an isolated per-module SQLite DB with both
HLD versions extracted (same pattern as the M5 graph tests). Evaluation
tests use the real registry + real ground truth (M6.17F).
"""

from __future__ import annotations

import json

import pytest

from backend.extraction.service import ExtractionService
from backend.findings.engine import FindingEngine
from backend.findings.evaluation import evaluate_all, evaluate_version
from backend.findings.models import FindingType
from backend.findings.persistence import (clear_findings, latest_run,
                                          load_findings, persist_findings)
from backend.rag.chunker import chunk_processed_json
from backend.storage.database import init_schema, make_engine, make_session_factory


@pytest.fixture(scope="module")
def registry_db(tmp_path_factory):
    """Isolated DB with both versions extracted once (module scope)."""
    db = tmp_path_factory.mktemp("findingsreg") / "registry.db"
    svc = ExtractionService(db_path=db)
    for ver in ("1.0.0", "1.1.0"):
        chunks = chunk_processed_json(
            f"data/processed/ABC_HLD_v{ver}__processed.json")
        res = svc.extract_from_chunks(chunks, document_name=f"d{ver}.pdf",
                                      version=ver, persist=True)
        assert res.status == "completed", res.issues
    return db


@pytest.fixture(scope="module")
def sf(registry_db):
    engine = make_engine(registry_db)
    init_schema(engine)
    return make_session_factory(engine)


# ------------------------------------------------------------ engine ----

class TestEngine:
    def test_run_both_versions(self, sf):
        s = sf()
        try:
            for vl in ("1.0.0", "1.1.0"):
                res = FindingEngine().run(s, vl)
                assert res.validation_ok, res.validation_issues
                assert res.entity_count > 0 and res.fact_count > 0
                assert set(res.detectors_run) == {
                    "conflicting_providers", "dangling_requires",
                    "duplicate_interface", "orphan_entity",
                    "unconsumed_signal", "undefined_reference"}
        finally:
            s.close()

    def test_v1_clean_v2_orphan(self, sf):
        """The canonical expected result on the synthetic corpus."""
        s = sf()
        try:
            r1 = FindingEngine().run(s, "1.0.0")
            r2 = FindingEngine().run(s, "1.1.0")
            assert r1.findings == []
            types = [f.finding_type for f in r2.findings]
            assert types.count(FindingType.ORPHAN_ENTITY) == 1
            orphan = [f for f in r2.findings
                      if f.finding_type == FindingType.ORPHAN_ENTITY][0]
            assert orphan.entity_keys == ["component:C-05"]
            assert orphan.provenance, "finding must carry provenance"
            p = orphan.provenance[0]
            assert p["document_name"] == "d1.1.0.pdf"
            assert p["version_label"] == "1.1.0"
        finally:
            s.close()

    def test_deterministic_outputs(self, sf):
        """Same DB + code => identical findings (IDs, order, counts)."""
        s = sf()
        try:
            a = FindingEngine().run(s, "1.1.0")
            b = FindingEngine().run(s, "1.1.0")
            assert ([f.finding_id for f in a.findings]
                    == [f.finding_id for f in b.findings])
            assert a.by_type() == b.by_type()
            assert a.by_severity() == b.by_severity()
        finally:
            s.close()

    def test_type_filter(self, sf):
        s = sf()
        try:
            full = FindingEngine().run(s, "1.1.0")
            part = FindingEngine().run(s, "1.1.0",
                                       finding_types=["orphan_entity"])
            assert [f.finding_id for f in part.findings] \
                == [f.finding_id for f in full.findings
                    if f.finding_type == FindingType.ORPHAN_ENTITY]
        finally:
            s.close()

    def test_unknown_version_raises(self, sf):
        s = sf()
        try:
            with pytest.raises(ValueError):
                FindingEngine().run(s, "77.7.7")
        finally:
            s.close()


# -------------------------------------------------------- persistence ----

class TestPersistence:
    def test_persist_and_load(self, sf):
        s = sf()
        try:
            res = FindingEngine().run(s, "1.1.0")
            stats = persist_findings(s, 2, res.findings)
            assert stats["persisted"] == len(res.findings)
            rows = load_findings(s, 2)
            assert [r.finding_id for r in rows] \
                == [f.finding_id for f in res.findings]
        finally:
            s.close()

    def test_idempotent_rerun(self, sf):
        """Re-running persists the same deterministic IDs; no duplicates."""
        s = sf()
        try:
            res = FindingEngine().run(s, "1.1.0")
            s1 = persist_findings(s, 2, res.findings)
            s2 = persist_findings(s, 2, res.findings)
            assert s2["persisted"] == s1["persisted"]
            assert s2["updated"] == s1["persisted"]  # all carried over
            rows = load_findings(s, 2)
            assert len({r.finding_id for r in rows}) == len(rows)
        finally:
            s.close()

    def test_review_state_preserved(self, sf):
        """Human review status survives a re-run (M8 workflow readiness)."""
        s = sf()
        try:
            res = FindingEngine().run(s, "1.1.0")
            persist_findings(s, 2, res.findings)
            # simulate a human review (M1 workflow fields)
            run = latest_run(s, 2)
            row = run.findings[0]
            from backend.storage.models import FindingStatus
            row.status = FindingStatus.ACCEPTED
            row.reviewer_comment = "confirmed by architect"
            s.commit()
            # re-run analysis
            res2 = FindingEngine().run(s, "1.1.0")
            persist_findings(s, 2, res2.findings)
            rows = load_findings(s, 2)
            target = [r for r in rows
                      if r.finding_id == run.findings[0].finding_id][0]
            assert target.status == FindingStatus.ACCEPTED
            assert target.reviewer_comment == "confirmed by architect"
        finally:
            s.close()

    def test_version_isolation(self, sf):
        """v1 run must never inspect or store v2 facts (and vice versa)."""
        s = sf()
        try:
            r1 = FindingEngine().run(s, "1.0.0")
            r2 = FindingEngine().run(s, "1.1.0")
            # v1 findings (none) cannot mention v2-only entities
            for f in r1.findings:
                assert "1.1.0" not in f.finding_id
            # each finding is scoped to exactly one version label
            for f in r1.findings + r2.findings:
                assert f.version_label in ("1.0.0", "1.1.0")
            # persistence is version-scoped
            persist_findings(s, 1, r1.findings)
            persist_findings(s, 2, r2.findings)
            v1_rows = load_findings(s, 1)
            v2_rows = load_findings(s, 2)
            assert all("ORPHAN" not in r.finding_id for r in v1_rows)
            assert any("ORPHAN" in r.finding_id for r in v2_rows)
            # the v1 DB has no v2 facts at all (structural isolation):
            # C-08|provides|IF-13 exists only in v2 (v1 provider is C-10)
            from sqlalchemy import select
            from backend.storage.models import ExtractionFact
            v1_facts = s.execute(select(ExtractionFact)
                                 .where(ExtractionFact.version_id == 1)
                                 ).scalars().all()
            assert all("C-08|provides|interface:IF-13" not in f.fact_key
                       for f in v1_facts)
            v2_facts = s.execute(select(ExtractionFact)
                                 .where(ExtractionFact.version_id == 2)
                                 ).scalars().all()
            assert any("C-08|provides|interface:IF-13" in f.fact_key
                       for f in v2_facts)
        finally:
            s.close()

    def test_reset(self, sf):
        s = sf()
        try:
            res = FindingEngine().run(s, "1.1.0")
            persist_findings(s, 2, res.findings)
            n = clear_findings(s, 2)
            assert n > 0
            assert load_findings(s, 2) == []
            assert latest_run(s, 2) is None
        finally:
            s.close()


# --------------------------------------------------------- evaluation ----

class TestEvaluation:
    def test_v1_baseline_zero(self, sf):
        s = sf()
        try:
            ev = evaluate_version(s, "1.0.0")
            m = ev.metrics()
            assert m["detected"] == 0
            assert m["tp"] == 0 and m["fp"] == 0 and m["fn"] == 0
        finally:
            s.close()

    def test_v2_orphan_detected(self, sf):
        s = sf()
        try:
            ev = evaluate_version(s, "1.1.0")
            m = ev.metrics()
            assert m["expected_applicable"] == 1      # D4 only
            assert m["tp"] == 1 and m["fp"] == 0 and m["fn"] == 0
            assert m["precision"] == 1.0
            assert m["recall"] == 1.0
            assert m["f1"] == 1.0
        finally:
            s.close()

    def test_not_applicable_documented(self, sf):
        """D3/D5 and cross-version defects must be reported, not hidden."""
        s = sf()
        try:
            out = evaluate_all(s)
            v2 = out["versions"]["1.1.0"]
            na_ids = {g["defect_id"] for g in v2["not_applicable"]}
            assert {"D3_conflicting_provider",
                    "D5_dropped_signal_referenced",
                    "D2_stale_component_reference"} <= na_ids
            v1 = out["versions"]["1.0.0"]
            # D1 is a v1-gold defect (gold_version: "v1") -> v1 bucket
            assert any(g["defect_id"] == "D1_removed_dependency"
                       for g in v1["not_applicable"])
        finally:
            s.close()

    def test_evaluation_json_serializable(self, sf):
        s = sf()
        try:
            out = evaluate_all(s)
            blob = json.dumps(out)          # must not raise
            assert "orphan_entity" in blob
        finally:
            s.close()
