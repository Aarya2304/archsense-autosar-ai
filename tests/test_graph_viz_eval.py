"""M5 tests: pyvis visualization, graph evaluation, CLI wiring.

Visualization tests assert on the generated HTML string (UTF-8, contains
labels/predicate/provenance markers) — no browser, no network. Evaluation
tests score a small synthetic graph against a hand-built gold set, plus
the real registry graph's provenance/integrity invariants.
"""

from __future__ import annotations

import json

import networkx as nx
import pytest
from sqlalchemy import select

from backend.extraction.service import ExtractionService
from backend.graph import evaluation as grapeval
from backend.graph.visualization import render_html
from backend.rag.chunker import chunk_processed_json
from backend.storage.database import init_schema, make_engine, make_session_factory
from backend.storage.models import ExtractionFact


@pytest.fixture(scope="module")
def registry_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("graphviz") / "registry.db"
    svc = ExtractionService(db_path=db)
    for ver in ("1.0.0", "1.1.0"):
        chunks = chunk_processed_json(
            f"data/processed/ABC_HLD_v{ver}__processed.json")
        res = svc.extract_from_chunks(chunks, document_name=f"d{ver}.pdf",
                                      version=ver, persist=True)
        assert res.status == "completed"
    return db


@pytest.fixture(scope="module")
def session_factory(registry_db):
    engine = make_engine(registry_db)
    init_schema(engine)
    return make_session_factory(engine)


@pytest.fixture(scope="module")
def g1(session_factory):
    from backend.graph.builder import build_graph
    g, _ = build_graph(session_factory(), version="1.0.0")
    return g


# ---------------------------------------------------------- visualization --

def test_render_html_utf8_and_content(g1, tmp_path):
    p = render_html(g1, tmp_path / "g.html")
    html = p.read_text(encoding="utf-8")   # must decode as UTF-8
    assert p.stat().st_size > 100_000      # vis.js inlined
    assert "component:C-02" in html        # node ids present
    assert "WindowLiftSWC" in html         # display labels
    assert "requires" in html              # predicate labels
    # provenance popups: chunk ids and section titles appear in edge titles
    assert "DoorStatusIF" in html or "WindowStatusIF" in html
    assert "confidence" in html
    # heading is ASCII-safe
    assert "ArchSense architecture graph" in html


def test_render_html_filtered_subgraph(g1, tmp_path):
    from backend.graph import filtering
    sub = filtering.filter_graph(g1, node="component:C-02", depth=1)
    p = render_html(sub, tmp_path / "sub.html")
    html = p.read_text(encoding="utf-8")
    assert "component:C-02" in html
    assert "component:C-05" not in html    # outside the neighbourhood


def test_render_html_is_offline(g1, tmp_path):
    """The exported HTML must be genuinely resource-free: vis bundle
    inlined, zero <script src>/<link href> external references (the pyvis
    template's dead bootstrap/CDN blocks are stripped), no secrets."""
    import re
    p = render_html(g1, tmp_path / "offline.html")
    html = p.read_text(encoding="utf-8")
    assert "Object.defineProperty" in html          # vis bundle inline
    assert "<script" in html and "</script>" in html
    for m in re.findall(r"<script([^>]*)>", html):
        assert "src=" not in m, m
    assert "https://cdn.jsdelivr.net" not in html
    assert not re.search(r"(OPENROUTER|API_KEY|sk-or-)", html)


# ------------------------------------------------------------- evaluation --

def _mini_graph() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    g.graph["version"] = "9.9.9"
    for key, et in (("component:C-1", "component"),
                    ("component:C-2", "component"),
                    ("interface:IF-1", "interface"),
                    ("signal:SG-1", "signal")):
        g.add_node(key, entity_type=et, key=key, name=key.split(":")[1],
                   version="9.9.9")
    prov = {"document_name": "t.pdf", "version_label": "9.9.9",
            "section_no": "4.1", "section_title": "x", "page_start": 1,
            "page_end": 1, "source_chunk_id": "ck"}
    for u, p, v, fk in (("component:C-1", "provides", "interface:IF-1", "f1"),
                        ("component:C-2", "requires", "interface:IF-1", "f2"),
                        ("interface:IF-1", "carries", "signal:SG-1", "f3")):
        g.add_edge(u, v, key=fk, predicate=p, fact_key=fk, confidence=0.9,
                    provenance=prov)
    return g


def test_evaluation_perfect_on_gold_graph():
    g = _mini_graph()
    gt_version = {
        "components": [{"id": "C-1"}, {"id": "C-2"}],
        "interfaces": [{"id": "IF-1", "provider": "C-1", "consumers": ["C-2"]}],
        "signals": [{"id": "SG-1", "interface_id": "IF-1"}],
        "dependencies": [],
        "flows": [],
    }
    ports = []           # no port entities in the mini graph
    res = grapeval.evaluate_graph(g, gt_version, ports,
                                  registry_fact_keys={"f1", "f2", "f3"})
    assert res.nodes["f1"] == 1.0 and res.nodes["precision"] == 1.0
    assert res.edges["precision"] == 1.0 and res.edges["f1"] == 1.0
    assert res.provenance_correctness == 1.0
    assert res.version_isolation["clean"]
    assert res.integrity["missing_from_graph"] == []
    assert res.integrity["not_in_registry"] == []


def test_evaluation_detects_missing_and_extra_edges():
    g = _mini_graph()
    g.remove_edge("component:C-2", "interface:IF-1", key="f2")   # miss
    prov = {"document_name": "t.pdf", "version_label": "9.9.9",
            "section_no": "", "section_title": "", "page_start": 1,
            "page_end": 1, "source_chunk_id": "ck"}
    g.add_edge("component:C-1", "component:C-2", key="fX",
               predicate="depends_on", fact_key="fX", confidence=0.9,
               provenance=prov)                                       # extra
    gt_version = {
        "components": [{"id": "C-1"}, {"id": "C-2"}],
        "interfaces": [{"id": "IF-1", "provider": "C-1", "consumers": ["C-2"]}],
        "signals": [{"id": "SG-1", "interface_id": "IF-1"}],
        "dependencies": [], "flows": [],
    }
    res = grapeval.evaluate_graph(g, gt_version, [],
                                  registry_fact_keys={"f1", "f2", "f3"})
    assert res.edges["fn"] == 1 and res.edges["fp"] == 1
    assert res.edges["precision"] < 1.0 and res.edges["recall"] < 1.0
    assert res.integrity["missing_from_graph"] == ["f2"]
    assert res.integrity["not_in_registry"] == ["fX"]
    by_pred = res.edges_by_predicate
    assert by_pred["requires"]["recall"] == 0.0


def test_evaluation_detects_provenance_gaps():
    g = _mini_graph()
    # strip provenance from one edge
    u, v, k = next(iter(g.edges(keys=True)))
    del g.edges[u, v, k]["provenance"]
    gt_version = {"components": [], "interfaces": [], "signals": [],
                  "dependencies": [], "flows": []}
    res = grapeval.evaluate_graph(g, gt_version, [], registry_fact_keys=set())
    assert res.provenance_correctness < 1.0


def test_evaluation_real_registry_invariants(g1, session_factory):
    """The registry graph must be 1:1 with extraction_facts and fully
    provenanced (the numeric scores are covered by the eval CLI)."""
    session = session_factory()
    keys = {r.fact_key for r in session.execute(select(ExtractionFact).where(
        ExtractionFact.version_label == "1.0.0")).scalars()}
    session.close()
    res = grapeval.evaluate_graph(
        g1, {"components": [], "interfaces": [], "signals": [],
             "dependencies": [], "flows": []}, [],
        registry_fact_keys=keys)
    assert res.integrity["missing_from_graph"] == []
    assert res.integrity["not_in_registry"] == []
    assert res.integrity["duplicate_fact_keys"] == 0
    assert res.provenance_correctness == 1.0
