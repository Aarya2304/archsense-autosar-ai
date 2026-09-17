"""Deterministic extractor (M4.4) — no LLM, fully mechanical (D-023).

Everything below is derived from *verified* chunk content shapes of the
synthetic corpus (both HLD versions):

- ``3.1`` catalogue table      -> component entities (ID, name, type, layer)
- ``3.2.x`` titles             -> "Component C-xx: Name" (corroborating)
- ``3.2.x`` port tables        -> port entities + implements/provides/
                                  requires facts (owning component found by
                                  walking back to the nearest component-title
                                  prose chunk — table-chunk section labels
                                  are page-lumped and NOT trusted, same
                                  lesson as M2's D-012 anchor fix)
- ``4.x`` titles + prose       -> interface entities (kind) + provides/
                                  requires facts ("Provider: C-xx.
                                  Consumers: ...")
- ``4.x`` signal tables        -> signal entities by NAME (no SG-ID present)
- ``5`` dictionary table       -> signal entities with SG-IDs + carries facts
- ``6.1`` table                -> dependency entities + depends_on/requires
                                  facts (explicit IDs)
- ``6.2.x`` prose              -> name-pair dependency facts (alias-resolved
                                  by the service; negation-guarded so the
                                  v2 D6 sentence "does not require" is never
                                  extracted)
- ``7.x`` titles + flow lines  -> functional-flow entities + participates_in
                                  facts ("Flow: C-xx (Name) -> ...")

Interface/signal/component NAME keys are emitted as seen and canonicalized
by the service via the alias map returned here (name-key -> ID-key).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.extraction.models import (EntityType, EvidenceRef, ExtractedEntity,
                                       ExtractedFact, Predicate, Source,
                                       normalize_key)
from backend.rag.models import Chunk

# ------------------------------------------------------- confidence (D-024) --
CONF_TABLE_WITH_ID = 0.95       # explicit ID in a table row
CONF_EXPLICIT_PROSE = 0.90      # "Provider: C-xx" / heading-declared entity
CONF_DERIVED_TABLE = 0.90       # relationship inferred from table columns
CONF_NAME_PROSE = 0.85          # catalogue-name pair in prose (alias-resolved)
CONF_NAME_ONLY = 0.80           # entity by name, ID unknown at this stage

# ------------------------------------------------------------- text patterns --
_COMP_TITLE_RE = re.compile(
    r"(?<![\w-])Component ([Cc]-\d{1,3}): ([A-Za-z][A-Za-z0-9_]*)")
_IFACE_TITLE_RE = re.compile(
    r"(?<![\w-])([A-Z][A-Za-z0-9]*IF) \(([Ii][Ff]-\d{1,3})\)")
_FLOW_TITLE_RE = re.compile(
    r"(?<![\w-])([A-Z][A-Za-z0-9][A-Za-z0-9 ,'&-]*?) \(([Ff][Ll]-\d{1,2})\)")
_ANY_HEADING_RE = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3})*  [A-Z]")

_PROVIDER_RE = re.compile(
    r"Provider:\s*([Cc]-\d{1,3})\s*\(([^)]+)\)")
_CONSUMERS_RE = re.compile(r"Consumers:\s*(.+?)\.")
_CONSUMER_CELL_RE = re.compile(r"([Cc]-\d{1,3})\s*\(([^)]+)\)")
_IFACE_KIND_RE = re.compile(r"\bis an? (S-R|C-S) interface\b")
_COMP_ATTRS_RE = re.compile(r"\((SWC|BSW|RTE|ECU-Abstraction), ([\w -]+?) layer\)")
_FLOW_STEP_RE = re.compile(r"(?<![\w-])([Cc]-\d{1,3})\s*\(")
_DEP_ROW_SPLIT = re.compile(r"\s*\|\s*")

_NEGATION_RE = re.compile(r"\b(not|never|no longer)\b", re.IGNORECASE)
_DEP_VERB_RE = re.compile(r"\b(requires|depends on|depends_on)\b")
_NO_PORTS_SENTINEL = "no explicitly assigned ports"


@dataclass
class TableSpec:
    """A parsed linearized table (chunk text: 'Table columns: ...' + rows)."""

    headers: list[str]
    rows: list[list[str]]
    chunk: Chunk

    @property
    def signature(self) -> tuple[str, ...]:
        return tuple(h.strip().lower() for h in self.headers)


@dataclass
class DeterministicExtraction:
    """Output of the deterministic extractor for one document version."""

    entities: list[ExtractedEntity] = field(default_factory=list)
    facts: list[ExtractedFact] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    # interface name-key -> provider component ID (from section-4 prose)
    iface_provider: dict[str, str] = field(default_factory=dict)
    # interface name-key -> consumer component IDs (from section-4 prose)
    iface_consumers: dict[str, list[str]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------ helpers --

def _parse_tables(chunks: Sequence[Chunk]) -> list[TableSpec]:
    specs: list[TableSpec] = []
    for c in chunks:
        if c.chunk_type != "table" or not c.text.startswith("Table columns:"):
            continue
        lines = c.text.splitlines()
        if len(lines) < 2:
            continue
        headers = _DEP_ROW_SPLIT.split(lines[0][len("Table columns:"):].strip())
        rows = [_DEP_ROW_SPLIT.split(ln) for ln in lines[1:] if ln.strip()]
        rows = [r for r in rows if len(r) == len(headers)]
        specs.append(TableSpec(headers=headers, rows=rows, chunk=c))
    return specs


def _region_after(text: str, end: int) -> str:
    """Text from ``end`` to the next numbered heading (or EOF)."""
    nxt = _ANY_HEADING_RE.search(text, end)
    return text[end:nxt.start() if nxt else len(text)]


def _src(chunk: Chunk) -> Source:
    return Source(
        document_name=chunk.document_name,
        version=chunk.version,
        sha256=chunk.sha256,
        section_no=chunk.section_no,
        section_title=chunk.section_title,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        chunk_id=chunk.chunk_id,
    )


def _ev(chunk: Chunk) -> EvidenceRef:
    return EvidenceRef(source=_src(chunk))


def _entity(et: EntityType, name: str, chunk: Chunk, conf: float,
            attributes: dict[str, str] | None = None,
            extractor: str = "deterministic") -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=et, name=name, attributes=attributes or {},
        evidence=_ev(chunk), confidence=conf, extractor=extractor,
        origin_chunk_id=chunk.chunk_id)


def _fact(subject: str, predicate: Predicate, obj: str, chunk: Chunk,
          conf: float, extractor: str = "deterministic") -> ExtractedFact:
    return ExtractedFact(
        subject=normalize_key(*_split_key(subject)),
        predicate=predicate,
        object=normalize_key(*_split_key(obj)),
        evidence=_ev(chunk), confidence=conf, extractor=extractor,
        origin_chunk_id=chunk.chunk_id)


def _split_key(key: str) -> tuple[EntityType, str]:
    etype, _, name = key.partition(":")
    return EntityType(etype), name


def _id_key(et: EntityType, raw: str) -> str:
    return f"{et.value}:{raw.strip().upper()}"


def _name_key(et: EntityType, raw: str) -> str:
    return normalize_key(et, raw)


# ----------------------------------------------------------- table handlers --


def _catalogue_table(spec: TableSpec, out: DeterministicExtraction) -> None:
    for r in spec.rows:
        cid, name, ctype, layer = r[0], r[1], r[2], r[3]
        out.entities.append(_entity(
            EntityType.COMPONENT, f"{cid} ({name})", spec.chunk,
            CONF_TABLE_WITH_ID,
            {"id": cid, "name": name, "type": ctype, "layer": layer,
             "description": r[4] if len(r) > 4 else ""}))
        out.aliases[_name_key(EntityType.COMPONENT, name)] = \
            _id_key(EntityType.COMPONENT, cid)
    out.stats["component_rows"] = out.stats.get("component_rows", 0) + len(spec.rows)


def _port_table(spec: TableSpec, out: DeterministicExtraction,
                owner_component: str | None) -> None:
    """Port table -> port entities + implements/provides/requires facts.

    Owning-component attribution uses the section-4 provider map (see the
    call-site comment in ``extract_deterministic``): the port table's
    single ``provides`` row names the interface the owner provides.
    """
    provided_ifaces: list[str] = []
    for r in spec.rows:
        pid, direction, iface_name, kind = r[0], r[1], r[2], r[3]
        out.entities.append(_entity(
            EntityType.PORT, pid, spec.chunk, CONF_TABLE_WITH_ID,
            {"id": pid, "direction": direction, "interface": iface_name,
             "kind": kind,
             "component_id": owner_component or ""}))
        # implements: port -> interface (by name; service alias-resolves)
        out.facts.append(_fact(
            _id_key(EntityType.PORT, pid), Predicate.IMPLEMENTS,
            _name_key(EntityType.INTERFACE, iface_name), spec.chunk,
            CONF_TABLE_WITH_ID))
        if direction == "provides":
            provided_ifaces.append(iface_name)
        if direction in ("provides", "requires") and owner_component:
            pred = (Predicate.PROVIDES if direction == "provides"
                    else Predicate.REQUIRES)
            out.facts.append(_fact(
                owner_component, pred,
                _name_key(EntityType.INTERFACE, iface_name), spec.chunk,
                CONF_DERIVED_TABLE))
    out.stats["port_rows"] = out.stats.get("port_rows", 0) + len(spec.rows)


def _dictionary_table(spec: TableSpec, out: DeterministicExtraction) -> None:
    for r in spec.rows:
        sid, name, datatype, unit, iface_name = r[0], r[1], r[2], r[3], r[4]
        out.entities.append(_entity(
            EntityType.SIGNAL, f"{sid} ({name})", spec.chunk,
            CONF_TABLE_WITH_ID,
            {"id": sid, "name": name, "datatype": datatype, "unit": unit}))
        out.aliases[_name_key(EntityType.SIGNAL, name)] = \
            _id_key(EntityType.SIGNAL, sid)
        out.facts.append(_fact(
            _name_key(EntityType.INTERFACE, iface_name), Predicate.CARRIES,
            _id_key(EntityType.SIGNAL, sid), spec.chunk, CONF_TABLE_WITH_ID))
    out.stats["signal_dict_rows"] = \
        out.stats.get("signal_dict_rows", 0) + len(spec.rows)


def _iface_signal_table(spec: TableSpec, out: DeterministicExtraction) -> None:
    for r in spec.rows:
        name, datatype, unit = r[0], r[1], r[2]
        out.entities.append(_entity(
            EntityType.SIGNAL, name, spec.chunk, CONF_NAME_ONLY,
            {"name": name, "datatype": datatype, "unit": unit}))
    out.stats["iface_signal_rows"] = \
        out.stats.get("iface_signal_rows", 0) + len(spec.rows)


def _dep_table(spec: TableSpec, out: DeterministicExtraction) -> None:
    for r in spec.rows:
        dep_id, src_cell, tgt_cell, rel = r[0], r[1], r[2], r[3]
        src_id = src_cell.split("(")[0].strip()
        tgt_id = tgt_cell.split("(")[0].strip()
        out.entities.append(_entity(
            EntityType.DEPENDENCY, dep_id, spec.chunk, CONF_TABLE_WITH_ID,
            {"id": dep_id, "source": src_id, "target": tgt_id,
             "relationship": rel}))
        # Component->component edges are uniformly DEPENDS_ON (taxonomy:
        # REQUIRES is component->interface); the corpus's original label
        # (requires/depends_on) is preserved on the dependency entity.
        out.facts.append(_fact(
            _id_key(EntityType.COMPONENT, src_id), Predicate.DEPENDS_ON,
            _id_key(EntityType.COMPONENT, tgt_id), spec.chunk,
            CONF_TABLE_WITH_ID))
    out.stats["dep_rows"] = out.stats.get("dep_rows", 0) + len(spec.rows)


_TABLE_HANDLERS = {
    ("id", "component", "type", "layer", "description"): _catalogue_table,
    ("port", "direction", "interface", "kind"): _port_table,
    ("signal id", "signal", "datatype", "unit", "interface"): _dictionary_table,
    ("signal", "datatype", "unit"): _iface_signal_table,
    ("dep. id", "source", "target", "type"): _dep_table,
}


# ------------------------------------------------------------- prose pass ----

def _component_owner_before(idx: int, chunks: Sequence[Chunk]) -> str | None:
    """Owning component ID for the port table at chunk ``idx``.

    Table-chunk section labels are page-lumped (D-012 lesson), so port
    tables find their owning component by walking back through the version's
    chunk sequence to the nearest prose chunk carrying a
    "Component C-xx: Name" heading.

    Corpus-specific twist: a component WITHOUT ports prints the renderer's
    fixed sentinel sentence ("no explicitly assigned ports") and the NEXT
    component's port table then sits between the two titles — the nearest
    preceding title is the wrong owner. When the preceding title chunk
    carries the sentinel, the owner is the nearest FOLLOWING component
    title (deterministic; sentinel text is renderer-fixed).
    """
    for j in range(idx, -1, -1):
        m = _COMP_TITLE_RE.search(chunks[j].text)
        if m:
            if _NO_PORTS_SENTINEL in chunks[j].text:
                break
            return _id_key(EntityType.COMPONENT, m.group(1))
        if _NO_PORTS_SENTINEL in chunks[j].text:
            break
    # sentinel path: owner is the next component title after the table
    for k in range(idx + 1, len(chunks)):
        m = _COMP_TITLE_RE.search(chunks[k].text)
        if m:
            return _id_key(EntityType.COMPONENT, m.group(1))
    return None


def _prose_facts_and_titles(chunks: Sequence[Chunk],
                            out: DeterministicExtraction,
                            catalogue_names: list[str]) -> None:
    name_alt = ("(?<![\w-])(" + "|".join(re.escape(n) for n in catalogue_names)
                + ")(?![\\w-])") if catalogue_names else None

    for idx, c in enumerate(chunks):
        text = c.text

        # --- component titles (corroborate catalogue; carry attrs) -------
        for m in _COMP_TITLE_RE.finditer(text):
            region = _region_after(text, m.end())
            attrs_m = _COMP_ATTRS_RE.search(region)
            attrs = {"id": m.group(1).upper(), "name": m.group(2)}
            if attrs_m:
                attrs["type"], attrs["layer"] = attrs_m.group(1), attrs_m.group(2)
            out.entities.append(_entity(
                EntityType.COMPONENT, f"{m.group(1)} ({m.group(2)})", c,
                CONF_EXPLICIT_PROSE, attrs))
            out.aliases[_name_key(EntityType.COMPONENT, m.group(2))] = \
                _id_key(EntityType.COMPONENT, m.group(1))

        # --- interface titles + provider/consumer facts ------------------
        for m in _IFACE_TITLE_RE.finditer(text):
            iid, iname = m.group(2).upper(), m.group(1)
            region = _region_after(text, m.end())
            attrs: dict[str, str] = {"id": iid, "name": iname}
            kind = _IFACE_KIND_RE.search(region)
            if kind:
                attrs["kind"] = kind.group(1)
            out.entities.append(_entity(
                EntityType.INTERFACE, f"{iname} ({iid})", c,
                CONF_EXPLICIT_PROSE, attrs))
            out.aliases[_name_key(EntityType.INTERFACE, iname)] = \
                _id_key(EntityType.INTERFACE, iid)

            prov = _PROVIDER_RE.search(region)
            if prov:
                out.iface_provider[_name_key(EntityType.INTERFACE, iname)] = \
                    prov.group(1).upper()
                out.facts.append(_fact(
                    _id_key(EntityType.COMPONENT, prov.group(1)),
                    Predicate.PROVIDES, _id_key(EntityType.INTERFACE, iid),
                    c, CONF_EXPLICIT_PROSE))
            cons = _CONSUMERS_RE.search(region)
            if cons:
                for cell in _CONSUMER_CELL_RE.finditer(cons.group(1)):
                    out.facts.append(_fact(
                        _id_key(EntityType.COMPONENT, cell.group(1)),
                        Predicate.REQUIRES, _id_key(EntityType.INTERFACE, iid),
                        c, CONF_EXPLICIT_PROSE))
                    out.iface_consumers.setdefault(
                        _name_key(EntityType.INTERFACE, iname), []).append(
                        cell.group(1).upper())

        # --- functional-flow titles + participation facts ----------------
        for m in _FLOW_TITLE_RE.finditer(text):
            fid = m.group(2).upper()
            region = _region_after(text, m.end())
            trig = re.search(r"Trigger:\s*(.+?)(?=\s*Flow:|$)", region)
            out.entities.append(_entity(
                EntityType.FUNCTIONAL_FLOW, f"{m.group(1)} ({fid})", c,
                CONF_EXPLICIT_PROSE,
                {"id": fid, "name": m.group(1),
                 "trigger": trig.group(1).strip() if trig else ""}))
            steps = list(dict.fromkeys(
                s.upper() for s in _FLOW_STEP_RE.findall(region)))
            for step in steps:
                out.facts.append(_fact(
                    _id_key(EntityType.COMPONENT, step),
                    Predicate.PARTICIPATES_IN,
                    _id_key(EntityType.FUNCTIONAL_FLOW, fid),
                    c, CONF_DERIVED_TABLE))

        # --- name-pair dependency facts (6.2.x prose; negation-guarded) --
        if name_alt and c.chunk_type == "prose":
            _extract_name_pair_facts(text, c, name_alt, out)


def _extract_name_pair_facts(text: str, chunk: Chunk, name_alt: str,
                             out: DeterministicExtraction) -> None:
    name_re = re.compile(name_alt)
    for verb in _DEP_VERB_RE.finditer(text):
        pre = text[max(0, verb.start() - 90):verb.start()]
        post = text[verb.end():verb.end() + 90]
        if _NEGATION_RE.search(pre[-40:]):
            continue                      # e.g. "does not require" (v2 D6)
        src_ms = list(name_re.finditer(pre))
        tgt_ms = list(name_re.finditer(post))
        if not src_ms or not tgt_ms:
            continue
        src_name = src_ms[-1].group(1)
        tgt_name = tgt_ms[0].group(1)
        if src_name == tgt_name:
            continue
        out.facts.append(_fact(
            _name_key(EntityType.COMPONENT, src_name), Predicate.DEPENDS_ON,
            _name_key(EntityType.COMPONENT, tgt_name), chunk, CONF_NAME_PROSE))
        out.stats["name_pair_facts"] = \
            out.stats.get("name_pair_facts", 0) + 1


# ------------------------------------------------------------------ entry ----

def extract_deterministic(chunks: Sequence[Chunk]) -> DeterministicExtraction:
    """Extract entities + facts from one version's chunks, no LLM (M4.4)."""
    ordered = sorted(chunks, key=lambda c: c.chunk_seq)
    out = DeterministicExtraction()

    # Component-name alternation needs the catalogue first: pass tables,
    # then prose (the catalogue table chunk always precedes 3.2.x prose).
    specs = _parse_tables(ordered)
    catalogue_names: list[str] = []
    for spec in specs:
        if spec.signature == ("id", "component", "type", "layer", "description"):
            _catalogue_table(spec, out)
            catalogue_names = [r[1] for r in spec.rows]

    # Prose pass FIRST: it fills out.iface_provider (section-4 facts),
    # which port-table attribution then relies on — document sequence is
    # NOT a reliable owner signal because tables physically flow across
    # component boundaries on shared pages (D-012 page-lumping lesson).
    _prose_facts_and_titles(ordered, out, catalogue_names)

    for spec in specs:
        handler = _TABLE_HANDLERS.get(spec.signature)
        if handler is None or handler is _catalogue_table:
            continue
        if handler is _port_table:
            # Owner = the component that PROVIDES the interface of the
            # table's P-port row (section-4 prose is authoritative and
            # exact; a P-port row is what makes a table a port table).
            owner = None
            for r in spec.rows:
                if r[1] == "provides":
                    owner_key = out.iface_provider.get(
                        _name_key(EntityType.INTERFACE, r[2]))
                    if owner_key:
                        owner = _id_key(EntityType.COMPONENT, owner_key)
                    break
            if owner is None:
                # Provides-less table (pure-consumer components, e.g. the
                # CanDriver): owner = the single component requiring EVERY
                # interface listed in the table (consumer-set intersection).
                sets = [set(out.iface_consumers.get(
                            _name_key(EntityType.INTERFACE, r[2]), []))
                        for r in spec.rows]
                sets = [s for s in sets if s]
                if sets:
                    inter = set.intersection(*sets)
                    if len(inter) == 1:
                        owner = _id_key(EntityType.COMPONENT,
                                        next(iter(inter)))
            _port_table(spec, out, owner)
        else:
            handler(spec, out)

    out.stats["entities"] = len(out.entities)
    out.stats["facts"] = len(out.facts)
    out.stats["aliases"] = len(out.aliases)
    return out
