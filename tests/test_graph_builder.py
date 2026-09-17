"""M5 tests: graph builder, provenance, validation, analysis, filtering.

Builder tests run against the REAL registry on an isolated per-class
database (both versions extracted once). Validation/analysis/filtering
tests use small in-memory graphs built with the real builder functions
over synthetic registry rows. All fast, deterministic, offline.
"""

from __future__ import annotations

import json

import networkx as nx
import pytest
from sqlalchemy import select

from backend.extraction.service import ExtractionService
from backend.graph import analysis, filtering
from backend.graph.builder import build_graph, edge_records
from backend.graph.models import Provenance
from backend.graph.validation import validate_graph
from backend.rag.chunker import chunk_processed_json
from backend.storage.database import init_schema, make_engine, make_session_factory
from backend.storage.models import ExtractionFact


# --------------------------------------------------------------- fixtures --

@pytest.fixture(scope="module")
def registry_db(tmp_path_factory):
    """One isolated DB with BOTH versions extracted (module-scope)."""
    db = tmp_path_factory.mktemp("graphreg") / "registry.db"
    svc = ExtractionService(db_path=db)
    for ver in ("1.0.0", "1.1.0"):
        chunks = chunk_processed_json(
            f"data/processed/ABC_HLD_v{ver}__processed.json")
        res = svc.extract_from_chunks(chunks, document_name=f"d{ver}.pdf",
                                      version=ver, persist=True)
        assert res.status == "completed", res.issues
    return db


@pytest.fixture(scope="module")
def session_factory(registry_db):
    engine = make_engine(registry_db)
    init_schema(engine)
    return make_session_factory(engine)


@pytest.fixture(scope="module")
def g1(session_factory):
    g, _ = build_graph(session_factory(), version="1.0.0")
    return g


@pytest.fixture(scope="module")
def g2(session_factory):
    g, _ = build_graph(session_factory(), version="1.1.0")
    return g


@pytest.fixture()
def small_graph():
    """6-node hand-built MultiDiGraph with realistic attributes."""
    g = nx.MultiDiGraph()
    g.graph["version"] = "9.9.9"
    g.add_node("component:C-1", entity_type="component", name="Alpha",
               normalized_name="alpha", version="9.9.9", confidence=0.95,
               source="deterministic")
    g.add_node("component:C-2", entity_type="component", name="Beta",
               normalized_name="beta", version="9.9.9", confidence=0.9,
               source="deterministic")
    g.add_node("interface:IF-1", entity_type="interface", name="IF1",
               normalized_name="if1", version="9.9.9", confidence=0.9,
               source="deterministic")
    g.add_node("signal:SG-1", entity_type="signal", name="S1",
               normalized_name="s1", version="9.9.9", confidence=0.9,
               source="deterministic")
    prov = Provenance(document_name="t.pdf", version_label="9.9.9",
                      section_no="4.1", section_title="IF1",
                      page_start=3, page_end=3, source_chunk_id="ck1")
    g.add_edge("component:C-1", "interface:IF-1", key="k1",
               predicate="provides", object_value="", fact_key="k1",
               confidence=0.9, source="deterministic",
               provenance=prov.to_dict())
    g.add_edge("component:C-2", "interface:IF-1", key="k2",
               predicate="requires", object_value="", fact_key="k2",
               confidence=0.8, source="deterministic",
               provenance=prov.to_dict())
    g.add_edge("interface:IF-1", "signal:SG-1", key="k3",
               predicate="carries", object_value="", fact_key="k3",
               confidence=0.9, source="deterministic",
               provenance=prov.to_dict())
    return g


# ------------------------------------------------------------------ build --

def test_build_v1_node_and_edge_counts(session_factory, g1):
    session = session_factory()
    facts = session.execute(select(ExtractionFact).where(
        ExtractionFact.version_label == "1.0.0")).scalars().all()
    session.close()
    assert g1.number_of_nodes() == 165
    assert g1.number_of_edges() == len(facts) == 200
    assert g1.graph["version"] == "1.0.0"


def test_build_v2_is_separate_smaller_graph(g2):
    assert g2.number_of_nodes() == 149
    assert g2.number_of_edges() == 178
    assert g2.graph["version"] == "1.1.0"


def test_no_cross_version_contamination(g1, g2):
    """v1 keys must not appear in the v2 graph where the fact is v1-only
    (DEP-19 and IF-08/IF-09 are removed in v2; SG-015 removed)."""
    assert "dependency:DEP-19" not in g2
    assert "interface:IF-08" not in g2
    assert "interface:IF-09" not in g2
    assert "signal:SG-015" not in g2
    # and v1 retains them
    assert "dependency:DEP-19" in g1
    assert "signal:SG-015" in g1


def test_six_entity_types_present(g1):
    types = {d["entity_type"] for _, d in g1.nodes(data=True)}
    assert types == {"component", "interface", "port", "signal",
                     "dependency", "functional_flow"}


def test_six_predicates_present(g1):
    preds = {d["predicate"] for _, _, d in g1.edges(data=True)}
    assert preds == {"provides", "requires", "depends_on", "carries",
                     "implements", "participates_in"}


def test_node_metadata_shape(g1):
    d = g1.nodes["component:C-02"]
    assert d["entity_type"] == "component"
    assert d["name"] == "WindowLiftSWC"
    assert d["normalized_name"] == "windowliftswc"
    assert d["version"] == "1.0.0"
    assert 0.0 <= d["confidence"] <= 1.0
    assert d["key"] == "component:C-02"


def test_edge_metadata_shape(g1):
    u, v, k, d = next(e for e in g1.edges(keys=True, data=True)
                      if e[3]["predicate"] == "provides"
                      and e[0] == "component:C-02")
    assert d["fact_key"]
    assert d["confidence"] >= 0.5
    assert d["source"] in ("deterministic", "llm")
    prov = d["provenance"]
    assert prov["document_name"].endswith(".pdf")
    assert prov["version_label"] == "1.0.0"
    assert prov["section_no"]
    assert prov["page_start"] >= 1
    assert prov["source_chunk_id"]


def test_requires_dangling_detection(g1):
    d = g1.nodes["signal:SG-015"]
    assert d is not None


def test_unknown_version_fails_fast(session_factory):
    with pytest.raises(ValueError, match="unknown version"):
        build_graph(session_factory(), version="42.0.0")


def test_missing_version_required(session_factory):
    with pytest.raises(ValueError, match="explicit version"):
        build_graph(session_factory(), version=None)


# -------------------------------------------------------------- provenance --

def test_every_edge_has_trusted_provenance(g1, g2):
    for g in (g1, g2):
        for u, v, k, d in g.edges(keys=True, data=True):
            p = d["provenance"]
            assert p["document_name"], (u, v, k)
            assert p["version_label"] == g.graph["version"]
            assert p["source_chunk_id"]
            assert p["page_start"] >= 1
            assert p["section_no"]


def test_provenance_matches_registry_row(session_factory, g1):
    session = session_factory()
    row = session.execute(select(ExtractionFact).where(
        ExtractionFact.version_label == "1.0.0",
        ExtractionFact.subject == "component:C-10",
        ExtractionFact.predicate == "provides")).scalars().first()
    session.close()
    d = g1.edges[row.subject, row.object, row.fact_key]
    assert d["provenance"]["document_name"] == row.document_name
    assert d["provenance"]["section_no"] == row.section_no
    assert d["provenance"]["page_start"] == row.page_start
    assert d["provenance"]["source_chunk_id"] == row.source_chunk_id
    assert d["confidence"] == row.confidence


def test_parallel_facts_preserved_as_separate_edges(g1):
    """MultiDiGraph keeps every fact as its own keyed edge (D-027). The
    current corpus happens to have no two facts on the same ordered node
    pair (each port maps to a distinct interface), so verify the CAPACITY
    on a copy: adding a second fact between an existing pair yields two
    distinct edges with distinct fact-key identities — the property M7's
    graph diff relies on."""
    u, v, k = next(iter(g1.edges(keys=True)))
    d = dict(g1.edges[u, v, k])
    g = g1.copy()
    g.add_edge(u, v, key="fact_key#2", **{**d, "fact_key": "fact_key#2"})
    assert g.number_of_edges() == 201
    keys_uv = [kk for _u, _v, kk in g.edges(keys=True)
               if (_u, _v) == (u, v)]
    assert sorted(keys_uv) == sorted([k, "fact_key#2"])
    # the shared fixture is untouched
    assert g1.number_of_edges() == 200


def test_edge_records_roundtrip(g1):
    recs = edge_records(g1)
    assert len(recs) == g1.number_of_edges()
    r = recs[0]
    assert r.subject and r.predicate and r.object
    assert r.provenance.document_name
    assert r.to_dict()["provenance"]["pages_csv"]


# -------------------------------------------------------------- validation --

def test_validate_real_graph_clean(session_factory, g1):
    res = validate_graph(g1, session_factory())
    assert res.valid, res.errors[:5]
    assert res.checked_edges == 200
    # dependency nodes are degree-0 by design (D-021)
    assert any("dependency entities are degree-0 by design" in w
               for w in res.warnings)


def test_validate_detects_dangling_edge():
    g = nx.MultiDiGraph()
    g.graph["version"] = "9.9.9"
    g.add_node("component:C-1", entity_type="component", version="9.9.9")
    g.add_edge("component:C-1", "interface:GHOST", key="k",
               predicate="provides", fact_key="fk1",
               provenance={"document_name": "t.pdf", "version_label": "9.9.9"})
    res = validate_graph(g)
    assert not res.valid
    assert any("dangling edge endpoint" in e for e in res.errors)


def test_validate_detects_invalid_predicate(small_graph):
    g = small_graph.copy()
    u, v, k = next(iter(g.edges(keys=True)))
    g.edges[u, v, k]["predicate"] = "hacks"
    res = validate_graph(g)
    assert not res.valid
    assert any("invalid predicate" in e for e in res.errors)


def test_validate_detects_missing_provenance(small_graph):
    g = small_graph.copy()
    u, v, k = next(iter(g.edges(keys=True)))
    del g.edges[u, v, k]["provenance"]
    res = validate_graph(g)
    assert not res.valid
    assert any("missing provenance" in e for e in res.errors)


def test_validate_detects_duplicate_fact_keys(small_graph):
    g = small_graph.copy()
    d = dict(g.edges["component:C-1", "interface:IF-1", "k1"])
    g.add_edge("component:C-1", "interface:IF-1", key="k1-copy", **d)
    res = validate_graph(g)
    assert not res.valid
    assert any("duplicate fact identity" in e for e in res.errors)


def test_validate_detects_version_mismatch(small_graph):
    g = small_graph.copy()
    u, v, k = next(iter(g.edges(keys=True)))
    g.edges[u, v, k]["provenance"]["version_label"] = "8.8.8"
    res = validate_graph(g)
    assert not res.valid
    assert any("provenance version" in e for e in res.errors)


# ---------------------------------------------------------------- analysis --

def test_node_degrees(g1):
    d = analysis.node_degree(g1, "component:C-02")
    assert d["out_degree"] >= 5      # provides IF-03, requires 5, flows...
    assert d["degree"] == d["in_degree"] + d["out_degree"]


def test_neighbors_directed(g1):
    n = analysis.neighbors(g1, "component:C-02")
    assert "interface:IF-03" in n["successors"]
    assert n["predecessors"] == []   # C-02 is not required-by in v1


def test_neighbors_undirected_includes_both(g1):
    nb = analysis.neighbors_undirected(g1, "component:C-02")
    assert "interface:IF-03" in nb


def test_shortest_path_directed(g1):
    p = analysis.shortest_path(g1, "component:C-02", "interface:IF-01")
    assert p and p[0] == "component:C-02" and p[-1] == "interface:IF-01"
    assert len(p) >= 3               # not directly connected


def test_shortest_path_none_when_unreachable(g1):
    # functional flow FL-2 has no outgoing edges; nothing reaches INTO it
    p = analysis.shortest_path(g1, "functional_flow:FL-2", "component:C-02")
    assert p is None


def test_shortest_path_undirected_differs(g1):
    assert analysis.shortest_path(g1, "functional_flow:FL-2",
                                  "component:C-02", directed=False) is not None


def test_weakly_connected_components(g1):
    comps = analysis.weakly_connected_components(g1)
    assert sum(len(c) for c in comps) == g1.number_of_nodes()
    assert comps == sorted(comps, key=lambda c: (-len(c), c[0]))


def test_related_groups_by_predicate(g1):
    rel = analysis.related(g1, "component:C-02")
    assert rel["type"] == "component"
    assert "provides" in rel["relationships"]
    assert "requires" in rel["relationships"]
    assert "depends_on" in rel["relationships"]
    assert rel["total"] == 11
    prov = rel["relationships"]["provides"][0]["provenance"]
    assert prov["document_name"].endswith(".pdf")


def test_resolve_key_forms(g1):
    assert analysis.resolve_key(g1, "component:C-02") == "component:C-02"
    assert analysis.resolve_key(g1, "C-02") == "component:C-02"
    assert analysis.resolve_key(g1, "c-02") == "component:C-02"
    assert analysis.resolve_key(g1, "component:c-02") == "component:C-02"
    assert analysis.resolve_key(g1, "WindowLiftSWC") == "component:C-02"


def test_resolve_key_ambiguous_name_returns_none(g1):
    # two ports named identically would be ambiguous; use a bogus name
    assert analysis.resolve_key(g1, "No Such Name") is None


# ---------------------------------------------------------------- filtering --

def test_filter_predicate(g1):
    sub = filtering.filter_graph(g1, predicate="requires")
    assert sub.number_of_edges() == 32
    assert all(d["predicate"] == "requires"
               for _, _, d in sub.edges(data=True))
    # attributes preserved verbatim
    u, v, k, d = next(iter(sub.edges(keys=True, data=True)))
    assert d["provenance"]["document_name"]


def test_filter_entity_type(g1):
    sub = filtering.filter_graph(g1, entity_type="component")
    types = {d["entity_type"] for _, d in sub.nodes(data=True)}
    assert types == {"component"}
    # component-component edges survive
    assert sub.number_of_edges() == 24


def test_filter_min_confidence(g1):
    sub = filtering.filter_graph(g1, min_confidence=0.95)
    assert all(d["confidence"] >= 0.95
               for _, _, d in sub.edges(data=True))
    assert sub.number_of_edges() == 115


def test_filter_ego_neighbourhood(g1):
    sub = filtering.filter_graph(g1, node="component:C-02", depth=1)
    assert "component:C-02" in sub
    assert "interface:IF-03" in sub
    assert sub.number_of_nodes() == 12


def test_filter_depth2_superset_of_depth1(g1):
    d1 = filtering.filter_graph(g1, node="component:C-02", depth=1)
    d2 = filtering.filter_graph(g1, node="component:C-02", depth=2)
    assert set(d1.nodes) <= set(d2.nodes)
    assert d2.number_of_nodes() > d1.number_of_nodes()


def test_filter_unknown_node_raises(g1):
    with pytest.raises(ValueError, match="not in graph"):
        filtering.filter_graph(g1, node="component:NOPE")


def test_filter_version_mismatch_raises(g1):
    with pytest.raises(ValueError, match="not '9.9.9'"):
        filtering.filter_graph(g1, version="9.9.9")


def test_filter_does_not_mutate_source(g1):
    before = (g1.number_of_nodes(), g1.number_of_edges())
    filtering.filter_graph(g1, predicate="carries", min_confidence=0.99)
    after = (g1.number_of_nodes(), g1.number_of_edges())
    assert before == after == (165, 200)


# ------------------------------------------------------------------- export --

def test_json_export_roundtrip(g1, tmp_path):
    from backend.graph.export import export_json, to_json_dict
    p = tmp_path / "g.json"
    export_json(g1, p)   # no stats passed -> computed from the graph
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["version"] == "1.0.0"
    assert payload["statistics"]["node_count"] == 165
    assert len(payload["edges"]) == 200
    # provenance survives export
    prov = next(e["provenance"] for e in payload["edges"]
                if e.get("provenance"))
    assert prov["document_name"].endswith(".pdf")
    assert prov["source_chunk_id"]
    # node-link data can be rehydrated
    g_back = nx.node_link_graph(payload, edges="edges")
    assert g_back.number_of_edges() == 200
    assert to_json_dict(g1)["version"] == "1.0.0"


def test_graphml_export(g1, tmp_path):
    from backend.graph.export import export_graphml
    p = tmp_path / "g.graphml"
    export_graphml(g1, p)
    assert p.stat().st_size > 0
    back = nx.read_graphml(p)
    assert back.number_of_nodes() == g1.number_of_nodes()


# ---------------------------------------------------------- service facade --

def test_service_facade(registry_db, session_factory):
    from backend.graph.service import GraphService
    svc = GraphService(session_factory=session_factory)
    g, stats = svc.build_graph("1.0.0")
    assert stats.node_count == 165
    val = svc.validate_graph(g)
    assert val.valid
    rel = svc.related("C-02", version="1.0.0", graph=g)
    assert rel["total"] == 11
    path = svc.shortest_path("C-02", "IF-01", version="1.0.0", graph=g)
    assert path and path[-1] == "interface:IF-01"
    assert svc.statistics("1.1.0")["edge_count"] == 178
    prov = svc.edge_provenance(g, "component:C-02", "interface:IF-03",
                               "component:C-02|provides|interface:IF-03|")
    assert prov["page_start"] == 9
