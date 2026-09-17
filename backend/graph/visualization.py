"""Pyvis visualization (M5.14/M5.15): interactive HTML with edge provenance.

UI-independent: produces a standalone HTML file (no server, no network at
runtime — pyvis embeds vis.js inline via ``cdn_resources='in_line'``), so
M8's Streamlit page can embed/reuse it later.

Edge provenance (D-028): the trusted M4 registry snapshot (document,
version, section, pages, chunk id) is rendered into the edge title popup
along with confidence and extractor — inspecting any relationship answers
"where does this come from?" without trusting any LLM text.

Node styling: one color per entity type; directed arrows with per-
predicate colors keep the six M4 relationship families visually distinct.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import networkx as nx

from backend.graph.models import ATTR_CONFIDENCE, ATTR_NAME, ATTR_TYPE

# pyvis 0.3.2's template ships two dead resource blocks: a commented-out
# node_modules/vis pair and a bootstrap CDN <link>+<script> that nothing
# in our graph uses. They are stripped deterministically so the exported
# HTML contains ZERO external references (air-gap/offline safe). The
# inlined vis-network bundle is untouched.
_DEAD_VIS_BLOCK = re.compile(
    r'<!-- <link rel="stylesheet" href="\.\./node_modules/vis.*?</script>-->',
    re.S)
_DEAD_BOOTSTRAP_CSS = re.compile(
    r'<link\s+href="https://cdn\.jsdelivr\.net/npm/bootstrap[^"]*"[^>]*>')
_DEAD_BOOTSTRAP_JS = re.compile(
    r'<script\s+src="https://cdn\.jsdelivr\.net/npm/bootstrap[^"]*"'
    r'[^>]*>\s*</script>', re.S)

# entity type -> display color (hex)
TYPE_COLORS = {
    "component": "#4C78A8",        # blue
    "interface": "#F58518",        # orange
    "port": "#54A24B",             # green
    "signal": "#B279A2",           # purple
    "dependency": "#E45756",       # red
    "functional_flow": "#72B7B2",  # teal
}

# predicate -> edge color
PREDICATE_COLORS = {
    "provides": "#4C78A8",
    "requires": "#F58518",
    "depends_on": "#E45756",
    "carries": "#B279A2",
    "implements": "#54A24B",
    "participates_in": "#72B7B2",
}


def _escape(s: str) -> str:
    return html.escape(str(s), quote=True)


def _node_title(d: dict) -> str:
    lines = [f"<b>{_escape(d.get(ATTR_NAME, ''))}</b>",
             f"type: {_escape(d.get(ATTR_TYPE, '?'))}",
             f"version: {_escape(d.get('version', '?'))}",
             f"confidence: {d.get(ATTR_CONFIDENCE, 0.0):.2f}"]
    for extra in ("description", "datatype", "unit", "kind", "direction"):
        if d.get(extra):
            lines.append(f"{extra}: {_escape(d[extra])}")
    return "<br>".join(lines)


def _edge_title(d: dict) -> str:
    """Edge popup: relationship, confidence, extractor, trusted provenance."""
    p = d.get("provenance", {}) or {}
    lines = [
        f"<b>{_escape(d.get('subject', ''))} "
        f"--{_escape(d.get('predicate', ''))}--&gt; "
        f"{_escape(d.get('object', ''))}</b>",
        f"confidence: {float(d.get(ATTR_CONFIDENCE, 0.0)):.2f}",
        f"extractor: {_escape(d.get('source', ''))}",
        "source: " + _escape(p.get("document_name", "?")),
        f"version: {_escape(p.get('version_label', '?'))}",
        "section: " + _escape(
            f"{p.get('section_no', '')} {p.get('section_title', '')}".strip()
            or "?"),
        "page: " + _escape(str(p.get("page_start", "?"))
                           if p.get("page_start") == p.get("page_end")
                           else (f"{p.get('page_start', '?')}-"
                                 f"{p.get('page_end', '?')}")),
        "chunk: " + _escape(str(p.get("source_chunk_id", "?"))),
    ]
    return "<br>".join(lines)


def render_html(g: nx.MultiDiGraph, path: Path, *,
                height: str = "750px", width: str = "100%",
                heading: str | None = None) -> Path:
    """Render the (optionally filtered) graph to a standalone HTML file.

    The default heading is deliberately ASCII. The HTML string is written
    explicitly as UTF-8: pyvis's own ``save_graph`` opens the target file
    with the platform default encoder (cp1252 on this Windows host), which
    chokes on non-ASCII content inside the inlined vis.js bundle.
    ``generate_html()`` (pyvis >= 0.3.1) returns the string so we control
    the file write; node/edge popup text is html-escaped before insertion.
    After generation the template's dead resource blocks (commented-out
    node_modules/vis pair, cosmetic bootstrap CDN tags) are removed, so
    the output file carries no external references at all.
    """
    from pyvis.network import Network

    net = Network(height=height, width=width, directed=True,
                  bgcolor="#ffffff", font_color="#222222",
                  cdn_resources="in_line", select_menu=False,
                  filter_menu=False)
    net.heading = heading or (
        f"ArchSense architecture graph - v{g.graph.get('version', '?')}")

    net.add_nodes = getattr(net, "add_nodes", None)  # noqa: F841 (docs hint)
    for n, d in g.nodes(data=True):
        etype = d.get(ATTR_TYPE, "unknown")
        net.add_node(
            n,
            label=str(d.get(ATTR_NAME) or n),
            title=_node_title(d),
            color=TYPE_COLORS.get(etype, "#888888"),
            shape=("dot" if etype in ("component", "interface",
                                      "functional_flow") else "dot"),
            size=22 if etype == "component" else 14,
            font={"size": 12},
        )

    for u, v, k, d in g.edges(keys=True, data=True):
        pred = d.get("predicate", "")
        net.add_edge(
            u, v,
            title=_edge_title(d),
            color=PREDICATE_COLORS.get(pred, "#999999"),
            arrows="to",
            label=str(pred),
            font={"size": 9, "vadjust": -2},
        )

    net.force_atlas_2based(gravity=-40, central_gravity=0.01,
                           spring_length=120, spring_strength=0.02,
                           damping=0.4)
    path.parent.mkdir(parents=True, exist_ok=True)
    html_str = net.generate_html()
    for pattern in (_DEAD_VIS_BLOCK, _DEAD_BOOTSTRAP_CSS, _DEAD_BOOTSTRAP_JS):
        html_str = pattern.sub("", html_str)
    path.write_text(html_str, encoding="utf-8")
    return path
