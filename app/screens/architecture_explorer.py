"""Architecture Explorer screen (M8.7/M8.8): M5 graph + entity details.

LEFT: filters (entity type, predicate, confidence, search, depth).
CENTER: interactive pyvis graph (backend-generated HTML only) with a
structured relationship table fallback.
RIGHT: selected entity details + relationships with provenance.
"""

from __future__ import annotations

import streamlit as st

from app import state
from app.components import fmt_provenance, profile_badge
from app.services import (ServiceError, get_architecture_graph,
                          get_entity_detail, get_versions_profiled,
                          search_entities)

_TYPE_COLORS = {
    "component": "#4C78A8",
    "interface": "#F58518",
    "port": "#54A24B",
    "signal": "#E45756",
    "dependency": "#72B7B2",
    "functional_flow": "#B279A2",
    # M9 AUTOSAR Adaptive Platform node types
    "adaptive_application": "#4C78A8",
    "functional_cluster": "#F58518",
    "ara": "#72B7B2",
    "platform_foundation": "#9D755D",
    "platform_service": "#E45756",
    "service_interface": "#54A24B",
    "manifest": "#B279A2",
}
_PRED_COLORS = {
    "provides": "#4C78A8",
    "requires": "#E45756",
    "depends_on": "#9D755D",
    "carries": "#F58518",
    "implements": "#54A24B",
    "participates_in": "#B279A2",
}


def _filtered_graph(graph, entity_type: str | None, predicate: str | None,
                    min_conf: float):
    """Subgraph view honoring the deterministic UI filters (no mutation)."""
    keep_nodes = [
        n for n, d in graph.nodes(data=True)
        if (not entity_type or d.get("entity_type") == entity_type)
        and float(d.get("confidence", 1.0)) >= min_conf
    ]
    sub = graph.subgraph(keep_nodes).copy()
    if predicate:
        drop = [e for e, k, d in sub.edges(keys=True, data=True)
                if d.get("predicate") != predicate]
        sub.remove_edges_from(drop)
    return sub


def render() -> None:
    try:
        versions = [v for v in get_versions_profiled() if v["has_registry"]]
    except ServiceError as exc:
        st.error(f"Database unavailable: {exc}")
        return
    if not versions:
        st.warning(
            "No structured architecture available. Run the M4 extraction "
            "pipeline first (scripts/extract_entities.py).")
        return

    labels = [f"{v['document_name']} — v{v['version']}" for v in versions]
    current = st.session_state.as_version
    idx = next((i for i, v in enumerate(versions)
                if v["version"] == current), 0)
    chosen = st.selectbox("Version", labels, index=idx)
    sel = versions[labels.index(chosen)]
    state.set_version(sel["version"])
    st.caption(f"Schema: {profile_badge(sel['profile'])} — node and edge "
               "types come from the profile's structured registry.")

    try:
        graph, stats = get_architecture_graph(sel["version"])
    except ServiceError as exc:
        st.error(str(exc))
        return

    # ------------------------------------------------------------ filters --
    fcols = st.columns([2, 2, 2, 3])
    entity_types = sorted({d.get("entity_type", "?")
                           for _, d in graph.nodes(data=True)})
    predicates = sorted({d.get("predicate", "?")
                         for _, _, d in graph.edges(data=True)})
    with fcols[0]:
        etype = st.selectbox("Entity type", ["(all)"] + entity_types,
                             key="ex_etype")
    with fcols[1]:
        pred = st.selectbox("Predicate", ["(all)"] + predicates,
                            key="ex_pred")
    with fcols[2]:
        min_conf = st.slider("Min confidence", 0.0, 1.0,
                             float(st.session_state.as_min_conf), 0.05,
                             key="ex_conf")
    with fcols[3]:
        query = st.text_input("Search entity", value="",
                              placeholder="e.g. C-05 or Door",
                              key="ex_search")

    st.session_state.as_entity_type = None if etype == "(all)" else etype
    st.session_state.as_predicate = None if pred == "(all)" else pred
    st.session_state.as_min_conf = min_conf

    sub = _filtered_graph(graph, st.session_state.as_entity_type,
                          st.session_state.as_predicate, min_conf)

    if query.strip():
        hits = search_entities(sel["version"], query)
        if hits:
            st.caption("Matches: " + ", ".join(hits[:40]))
        else:
            st.caption("No entities match the search.")

    st.caption(f"Showing {sub.number_of_nodes()} nodes / "
               f"{sub.number_of_edges()} edges "
               f"(full graph: {graph.number_of_nodes()} nodes / "
               f"{graph.number_of_edges()} edges; "
               f"built in {getattr(stats, 'build_time_ms', 0):.0f} ms)")

    # ------------------------------------------------------------- graph --
    gcol, dcol = st.columns([5, 3], gap="medium")
    with gcol:
        try:
            _render_pyvis(sub)
        except Exception as exc:  # pragma: no cover - pyvis/browser issue
            st.warning(f"Interactive graph unavailable ({exc}). "
                       "Relationship table below.")
        with st.expander("Relationship table (fallback / detail)"):
            rows = []
            for frm, to, key, d in sorted(
                    sub.edges(keys=True, data=True),
                    key=lambda e: (str(e[0]), str(e[2]))):
                rows.append({
                    "subject": frm, "predicate": d.get("predicate"),
                    "object": to, "confidence": d.get("confidence"),
                    "extractor": d.get("extractor"),
                    "provenance": fmt_provenance(
                        (d.get("provenance") or {}).get("document_name"),
                        (d.get("provenance") or {}).get("version_label"),
                        (d.get("provenance") or {}).get("section_no"),
                        (d.get("provenance") or {}).get("section_title"),
                        (d.get("provenance") or {}).get("page_start"),
                        (d.get("provenance") or {}).get("page_end"),
                        (d.get("provenance") or {}).get("source_chunk_id"),
                    ),
                })
            if rows:
                st.dataframe(rows, use_container_width=True, height=280)
            else:
                st.caption("No edges match the current filters.")

    # ------------------------------------------------------------- detail --
    with dcol:
        _render_detail(sel["version"], sub)

    st.caption(
        "Graph source of truth: the M4 SQLite registry, rebuilt per version "
        "through the M5 GraphService. Provenance shown for each relationship "
        "is the trusted registry snapshot — not LLM output.")


def _render_pyvis(sub) -> None:
    """Interactive pyvis HTML from the filtered graph (offline, no CDN)."""
    from pyvis.network import Network

    net = Network(height="520px", width="100%", directed=True,
                  cdn_resources="in_line")
    for node, data in sub.nodes(data=True):
        etype = data.get("entity_type", "?")
        net.add_node(
            node,
            label=str(data.get("name", node)),
            title=f"{node}\n{etype} · confidence "
                  f"{data.get('confidence', '-')}",
            color=_TYPE_COLORS.get(etype, "#888"),
        )
    for frm, to, key, d in sub.edges(keys=True, data=True):
        pred = d.get("predicate", "?")
        prov = d.get("provenance") or {}
        title = (
            f"{frm} --{pred}--> {to}\n"
            f"confidence {d.get('confidence', '-')} · "
            f"extractor {d.get('extractor', '-')}\n"
            f"{prov.get('document_name', '')} · "
            f"v{prov.get('version_label', '')} · "
            f"Section {prov.get('section_no', '')} "
            f"{prov.get('section_title', '')} · "
            f"pp.{prov.get('page_start', '?')}-{prov.get('page_end', '?')} · "
            f"chunk {str(prov.get('source_chunk_id', ''))[:8]}…"
        )
        net.add_edge(frm, to, title=title, label=pred,
                     color=_PRED_COLORS.get(pred, "#666"),
                     arrows="to")
    net.force_atlas_2based(gravity=-30, spring_length=120)
    html = net.generate_html(name="graph.html", local=False, notebook=False)
    st.iframe(html, height=540)


def _render_detail(version: str, sub) -> None:
    selected = st.session_state.as_selected_entity
    nodes = sorted(sub.nodes)
    options = ["(select an entity)"] + nodes
    idx = options.index(selected) + 1 if selected in nodes else 0
    pick = st.selectbox("Entity details", options, index=idx,
                        key="ex_entity_pick")
    if pick == "(select an entity)":
        st.session_state.as_selected_entity = None
        st.caption("Select an entity to see its attributes, relationships "
                   "and provenance.")
        return
    st.session_state.as_selected_entity = pick

    from app.services import get_entity_detail
    detail = get_entity_detail(version, pick)
    if detail is None:
        st.error(f"Entity {pick} not found in the {version} graph.")
        return

    attrs = detail["attributes"]
    st.markdown(f"#### {attrs.get('name', pick)}")
    st.caption(pick)
    rows = {
        "Entity type": attrs.get("entity_type"),
        "Normalized name": attrs.get("normalized_name"),
        "Confidence": attrs.get("confidence"),
        "Extractor": attrs.get("extractor"),
        "Version": attrs.get("version", version),
    }
    for k, v in rows.items():
        if v is not None:
            st.markdown(f"**{k}:** {v}")
    prov = attrs.get("provenance") or {}
    if prov:
        st.caption("Provenance: " + fmt_provenance(
            prov.get("document_name"), prov.get("version_label"),
            prov.get("section_no"), prov.get("section_title"),
            prov.get("page_start"), prov.get("page_end"),
            prov.get("source_chunk_id")))

    for title, rels in (("Outgoing relationships", detail["outgoing"]),
                        ("Incoming relationships", detail["incoming"])):
        st.markdown(f"**{title}** ({len(rels)})")
        for rel in rels:
            arrow = (f"{rel['subject']} --{rel['predicate']}--> "
                     f"{rel['object']}")
            with st.expander(arrow):
                st.markdown(f"Confidence: {rel.get('confidence')}")
                p = rel.get("provenance") or {}
                st.caption("Provenance: " + fmt_provenance(
                    p.get("document_name"), p.get("version_label"),
                    p.get("section_no"), p.get("section_title"),
                    p.get("page_start"), p.get("page_end"),
                    p.get("source_chunk_id")))
