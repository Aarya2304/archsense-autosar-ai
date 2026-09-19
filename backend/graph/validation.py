"""Mechanical graph validation (M5.9): integrity checks, no LLM involved.

Mirrors the M3/M4 validator philosophy: pure application logic over the
built graph + registry facts, structured results, honest error reporting.
Every check maps to a demo/evaluator question ("can the graph lie?").
"""

from __future__ import annotations

import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.extraction.autosar.models import (AutosarEntityType,
                                               AutosarPredicate,
                                               PREDICATE_DOMAIN as AU_DOM,
                                               PREDICATE_RANGE as AU_RNG)
from backend.extraction.models import Predicate
from backend.graph.models import (ATTR_CONFIDENCE, ATTR_FACT_KEY,
                                  ATTR_OBJECT_VALUE, ATTR_PREDICATE,
                                  ATTR_PROVENANCE, ATTR_TYPE,
                                  ATTR_VERSION, GraphValidationResult)
from backend.storage.models import ExtractionFact

VALID_PREDICATES = ({p.value for p in Predicate}
                    | {p.value for p in AutosarPredicate})


def validate_graph(g: nx.MultiDiGraph, session: Session | None = None
                   ) -> GraphValidationResult:
    """Check graph integrity against its own nodes/edges and, when a
    session is given, against the M4 ``extraction_facts`` registry."""
    res = GraphValidationResult(
        checked_nodes=g.number_of_nodes(),
        checked_edges=g.number_of_edges())
    version = g.graph.get("version")

    seen_fact_keys: set[str] = set()
    for u, v, k, d in list(g.edges(keys=True, data=True)):
        label = f"{u} -[{d.get(ATTR_PREDICATE)}]-> {v} ({k})"
        # 1. endpoints exist as proper nodes. NX auto-creates attribute-
        #    less endpoint nodes on add_edge, so the testable form of
        #    "dangling" is: an endpoint node that carries no typed
        #    attributes (never registered by the builder).
        endpoint_types = (g.nodes[u].get(ATTR_TYPE),
                          g.nodes[v].get(ATTR_TYPE))
        if any(t is None for t in endpoint_types):
            res.errors.append(
                f"dangling edge endpoint (node has no typed "
                f"attributes): {label}")
        elif "unregistered" in endpoint_types:
            res.warnings.append(
                f"edge endpoint not in typed registry: {label}")
        # 2. valid predicate (profile union; ABC + AUTOSAR vocabularies)
        pred = d.get(ATTR_PREDICATE)
        if pred not in VALID_PREDICATES:
            res.errors.append(f"invalid predicate {pred!r}: {label}")
        # 2b. AUTOSAR-profile facts also honor domain/range constraints
        elif (str(u).startswith("autosar:") and str(v).startswith("autosar:")
                and pred in AU_DOM):
            su, sv = str(u).split(":"), str(v).split(":")
            if len(su) == 3 and len(sv) == 3:
                try:
                    if (AutosarEntityType(su[1]) not in AU_DOM[pred]
                            or AutosarEntityType(sv[1]) not in AU_RNG[pred]):
                        res.errors.append(
                            f"domain/range violation: {label}")
                except ValueError:
                    res.errors.append(f"invalid autosar endpoint type: {label}")
        # 3. provenance present + version match
        prov = d.get(ATTR_PROVENANCE)
        if not prov or not isinstance(prov, dict) or not prov.get("document_name"):
            res.errors.append(f"missing provenance: {label}")
        elif version is not None and prov.get("version_label") != version:
            res.errors.append(
                f"provenance version {prov.get('version_label')!r} != graph "
                f"version {version!r}: {label}")
        # 4. edge identity matches the M4 fact identity
        fk = d.get(ATTR_FACT_KEY, "")
        if not fk:
            res.errors.append(f"missing fact_key (edge identity): {label}")
        elif fk in seen_fact_keys:
            res.errors.append(f"duplicate fact identity: {fk} ({label})")
        else:
            seen_fact_keys.add(fk)

    # 5. canonical node keys ("type:ID" shape for the ABC profile;
    #    "autosar:<type>:<name>" for the M9 AUTOSAR profile)
    for n in g.nodes:
        if str(n).startswith("autosar:"):
            parts = str(n).split(":")
            if len(parts) != 3 or not all(parts):
                res.warnings.append(f"non-canonical autosar node key: {n!r}")
            elif g.nodes[n].get(ATTR_TYPE) not in (None, parts[1]):
                res.warnings.append(
                    f"node type mismatch: {n!r} typed "
                    f"{g.nodes[n].get(ATTR_TYPE)!r}")
            continue
        etype, _, eid = str(n).partition(":")
        if not etype or not eid or etype != etype.strip():
            res.warnings.append(f"non-canonical node key: {n!r}")
        if g.nodes[n].get(ATTR_TYPE) not in (None, etype):
            res.warnings.append(
                f"node type mismatch: {n!r} typed "
                f"{g.nodes[n].get(ATTR_TYPE)!r}")

    # 6. registry cross-check: every fact for this version is present
    if session is not None and version is not None:
        rows = session.execute(select(ExtractionFact).where(
            ExtractionFact.version_label == version)).scalars().all()
        reg_keys = {r.fact_key for r in rows}
        missing = reg_keys - seen_fact_keys
        extra = seen_fact_keys - reg_keys
        for fk in sorted(missing):
            res.errors.append(
                f"registry fact missing from graph: {fk}")
        for fk in sorted(extra):
            res.errors.append(
                f"graph edge not in registry: {fk}")
        # orphan check (informational for M6, warning here).
        # Dependency-typed nodes are EXEMPT by design (D-021): their
        # source/target semantics live as node metadata and the canonical
        # depends_on edge connects the two components directly, so
        # dependency entities are degree-0 without being findings. Real
        # orphans (e.g. v2's planted orphan component C-05) still warn.
        registered = {r.subject for r in rows} | {r.object for r in rows}
        orphans = [n for n in g.nodes
                   if g.degree(n) == 0 and n not in registered
                   and not str(n).startswith("dependency:")]
        dep_orphans = sum(1 for n in g.nodes
                          if g.degree(n) == 0
                          and str(n).startswith("dependency:"))
        if dep_orphans:
            res.warnings.append(
                f"{dep_orphans} dependency entities are degree-0 by design "
                f"(D-021: relationship carried as node metadata)")
        for n in sorted(orphans):
            res.warnings.append(f"orphan node (degree 0): {n}")

    res.valid = not res.errors
    return res


def dangling_references(g: nx.MultiDiGraph) -> list[str]:
    """Edges whose subject/object lacks typed node attributes — the NX
    form of a dangling reference (M6 helper)."""
    return [f"{u} -[{d.get(ATTR_PREDICATE)}]-> {v}"
            for u, v, d in g.edges(data=True)
            if g.nodes[u].get(ATTR_TYPE) is None
            or g.nodes[v].get(ATTR_TYPE) is None]
