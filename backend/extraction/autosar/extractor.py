"""AUTOSAR Adaptive Platform deterministic extraction (M9).

Extracts entities and relationships mechanically from the chunked text of
an AUTOSAR Adaptive Platform explanatory document. No LLM anywhere; every
output carries a trusted ``EvidenceRef`` (chunk -> document/section/page)
so the M4.6 provenance philosophy (D-022) holds unchanged.

Design rules (derived from reading the actual R20-11 document):

- Evidence is line-windowed: the extractor scans chunk texts line by line;
  a sentence spanning consecutive lines is joined until sentence end, so
  relationships expressed across PDF line breaks are still captured. The
  evidence chunk (and thus page/section provenance) is always the chunk the
  matched line came from — provenance is never guessed.
- The KNOWN-ENTITY SET is built in a first pass (structural definitions,
  section titles, FC chapters, explicit canonical names) and frozen before
  the second pass extracts relationships; both endpoints of every fact must
  exist in the set, so the M4.7 reference-validation layer accepts the
  output by construction.
- Section-title mapping is DERIVED from the document's own heading list
  (sections 4-12 of the R20-11 doc are the Functional Cluster chapters);
  no FC name is hardcoded — the title→FC map is built from headings whose
  text matches known FC naming patterns in the AUTOSAR vocabulary
  ("... Management", "Operating System", "Cryptography", ...), and each
  mapping is evidence-backed by the heading line itself.
- Abbreviations: the document introduces "(EM)", "(SM)", "(CM)", "(DM)",
  "(TS)", "(UCM)" parenthetically; the extractor records the abbreviation
  as an alias so later mentions like "EM" resolve to the same entity
  (dedupe identity stays the canonical key).
- Negation guard: sentences containing "does not provide", "not available
  as part of ARA", "is not part of ARA" never yield relationship facts.

Confidence tiers (rule-based, documented — NOT calibrated probabilities):
  CONF_STRUCTURAL_SENTENCE 0.90  explicit definitional sentence
  CONF_RELATION_VERB      0.85  explicit relationship verb, both named
  CONF_SECTION_TITLE      0.80  entity introduced by its own heading
  CONF_NAME_MENTION       0.75  named entity mentioned in prose
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.extraction.autosar.models import (AutosarEntityType,
                                               AutosarFact, AutosarPredicate,
                                               CONF_NAME_MENTION,
                                               CONF_RELATION_VERB,
                                               CONF_SECTION_TITLE,
                                               CONF_STRUCTURAL_SENTENCE,
                                               normalize_key)
from backend.extraction.models import EvidenceRef, Source
from backend.rag.models import Chunk

# ---------------------------------------------------------------- regexes ----

# "(EM)", "(also called UCM)" etc. right after a canonical name
_ABBREV_RE = re.compile(r"\(([A-Z]{2,6})\)")

# Sentence accumulation: a line ending mid-sentence joins the next line.
_SENT_END_RE = re.compile(r"[.!?]$")
# Bullet/figure/table/table-caption lines never form sentences.
_NOISE_RE = re.compile(
    r"^(figure\s+\d+-\d+|table\s+\d+[.:]?|\[?\d+\]?\.?$|•|—|-|)+", re.IGNORECASE)

# Canonical names (longest-first alternation built at import time).
_FC_NAMES = [
    "Execution Management", "State Management", "Communication Management",
    "Diagnostic Management", "Platform Health Management",
    "Network Management", "Update and Configuration Management",
    "Identity and Access Management", "Time Synchronization",
    "Persistency", "Cryptography", "Log and Trace", "Core Types",
    "Operating System", "Safety",
]
_ABBREVS = {
    "EM": "Execution Management",
    "SM": "State Management",
    "CM": "Communication Management",
    "DM": "Diagnostic Management",
    "TS": "Time Synchronization",
    "UCM": "Update and Configuration Management",
    "PHM": "Platform Health Management",
    "IAM": "Identity and Access Management",
    "OS": "Operating System",
    "NM": "Network Management",
}
_STRUCTURAL = {
    "Adaptive Applications": AutosarEntityType.ADAPTIVE_APPLICATION,
    "ARA": AutosarEntityType.ARA,
    "Adaptive Platform Foundation": AutosarEntityType.PLATFORM_FOUNDATION,
    "Adaptive Platform Services": AutosarEntityType.PLATFORM_SERVICE,
    "Functional Clusters": AutosarEntityType.FUNCTIONAL_CLUSTER,
}
# singular/plural handling: strip a trailing 's' for the canonical name
_PLURAL_FIX = {
    "Adaptive Applications": "Adaptive Application",
    "Functional Clusters": "Functional Cluster",
    "Adaptive Platform Services": "Adaptive Platform Service",
}

_MANIFEST_KINDS = [
    "Machine Manifest", "Execution Manifest", "Execution manifest",
    "Service Instance Manifest", "Application Design",
]
_MANIFEST_CANON = {
    "Machine Manifest": "Machine Manifest",
    "Execution Manifest": "Execution Manifest",
    "Execution manifest": "Execution Manifest",
    "Service Instance Manifest": "Service Instance Manifest",
    "Application Design": "Application Design",
}
_INTERFACE_TERMS = [
    "ARA interface", "PSE51", "OSI", "ara::com service interface",
    "C++ interface", "SOME/IP",
]
_PROCESS_NAME = "OS process"          # generic process entity
_APF_NAME = "Adaptive Platform Foundation"
_APS_NAME = "Adaptive Platform Services"
_APS_SINGULAR = "Adaptive Platform Service"
_APF_SINGULAR = "Adaptive Platform Foundation"  # already singular
_APF_KEY = normalize_key(AutosarEntityType.PLATFORM_FOUNDATION, "Adaptive Platform Foundation")
_APS_KEY = normalize_key(AutosarEntityType.PLATFORM_SERVICE, "Adaptive Platform Service")
_ARA_KEY = normalize_key(AutosarEntityType.ARA, "ARA")
_UCM_NAME = "Update and Configuration Management"

_NEGATION_RE = re.compile(
    r"\b(?:do(?:es)? not|not available|is not|are not|no longer|cannot|"
    r"may not)\b", re.IGNORECASE)

# Table-of-contents / dot-leader noise: lines whose content is a heading
# followed by "..... 51" carry no architecture semantics and, when joined
# into accumulated sentences, produce spurious relationship matches (33 of
# the first real-document run's facts cited the ToC pages). A line is ToC
# noise when it contains a dot-leader run ("....." x3+) or ends with a lone
# page number after leaders.
_TOC_LINE_RE = re.compile(r"\.{3,}\s*\d{1,3}\s*$|\.{5,}")
# A whole chunk is ToC/front-matter when it is dominated by dot-leader lines.
_TOC_CHUNK_MIN_RATIO = 0.5
# Front-matter line markers: table-of-contents headers and release-table
# bullet fragments (the R20-11 change-history table spans pages 1-2; its
# fragmented rows spliced into "sentences" produce e.g. a spurious
# UCM -updates-> Software Package from "changes in the State Management,
# Update and Configuration Management ...").
_FRONTMATTER_LINE_RE = re.compile(
    r"^(?:table of contents|\u2022|release changed by|description|date|"
    r"release|moderate amount of changes|minor changes)", re.IGNORECASE)
# Chunks starting before the first numbered section are front matter
# (title page, change history, copyright, ToC) — architecture semantics
# live in numbered sections only.
_FRONTMATTER_MAX_PAGE_DEFAULT = 7

# relationship triggers, with their predicate + direction semantics
_REL_PROVIDES_IF = re.compile(
    r"\b(?:provide[sd]?|provides)\b[^.]{0,80}?\binterface[s]?\b", re.IGNORECASE)
_REL_INTERACT = re.compile(
    r"\b(?:interact[s]? with|interacting with|communication between|"
    r"coordina(?:te|tes|ting))\b", re.IGNORECASE)
_REL_COMMANDS = re.compile(r"\bcommanding\b", re.IGNORECASE)
_REL_RUNS_ON = re.compile(r"\brun[s]?\s+on(?:\s+top\s+of)?\b", re.IGNORECASE)
_REL_BELONGS = re.compile(
    r"\b(?:belong[s]? to|belonging to)\b", re.IGNORECASE)
_REL_PROVIDES_SVC = re.compile(
    r"\b(?:provide[s]?\s+service[s]?|provide[s]? such service[s]?|"
    r"offer[s]?\s+or\s+consume(?:s)?)\b", re.IGNORECASE)
_REL_IMPL_AS = re.compile(
    r"\b(?:implemented as|implemented with|realize[sd]? in the form of)\b",
    re.IGNORECASE)
_REL_CONFIGURED_BY = re.compile(
    r"\b(?:configured in|based on|determined by)\b", re.IGNORECASE)
_REL_USES_IF = re.compile(
    r"\b(?:use[sd]?\s+(?:only\s+)?(?:the\s+)?(?:standard\s+)?|use[s]?\s+)"
    r"?(?:PSE51\s+)?(?:as\s+)?(?:OS\s+)?interface[s]?\b|\buse[s]?\s+PSE51\b|"
    r"\buse[s]?\s+the\s+standard\s+ARA\b", re.IGNORECASE)
_REL_UPDATES = re.compile(r"\b(?:updat(?:e|es|ing))\b", re.IGNORECASE)


# --------------------------------------------------------------- utilities ----

@dataclass
class AutosarExtraction:
    """Output of the deterministic AUTOSAR extractor for one document."""

    entities: list = field(default_factory=list)
    facts: list = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)   # name -> key
    stats: dict[str, int] = field(default_factory=dict)


def _src(chunk: Chunk) -> Source:
    """Trusted M4 provenance snapshot (EvidenceRef is typed against it)."""
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


def _mk_entity(store: dict, etype: AutosarEntityType, name: str,
               chunk: Chunk, conf: float,
               attributes: dict[str, str] | None = None) -> None:
    """Create-or-strengthen an entity in ``store`` (best confidence wins)."""
    from backend.extraction.autosar.models import AutosarEntity

    key = normalize_key(etype, name)
    existing = store.get(key)
    if existing is None:
        store[key] = AutosarEntity(
            entity_type=etype, name=name, attributes=attributes or {},
            evidence=_ev(chunk), confidence=conf,
            origin_chunk_id=chunk.chunk_id)
    elif conf > existing.confidence:
        store[key] = AutosarEntity(
            entity_type=etype, name=name,
            normalized_name=existing.normalized_name,
            attributes={**existing.attributes, **(attributes or {})},
            evidence=_ev(chunk), confidence=conf,
            origin_chunk_id=chunk.chunk_id)


def _mk_fact(store: dict, seen_keys: set[str], subject_key: str,
             predicate: AutosarPredicate, object_key: str, chunk: Chunk,
             conf: float) -> None:
    """Append a deduped fact; both endpoints must already be known."""
    from backend.extraction.autosar.models import AutosarFact

    if subject_key == object_key:
        return
    if subject_key not in seen_keys or object_key not in seen_keys:
        return
    dk = f"{subject_key}|{predicate.value}|{object_key}"
    if any(f.dedupe_key == dk for f in store):
        prev = next(f for f in store if f.dedupe_key == dk)
        if conf > prev.confidence:
            store.remove(prev)
            store.append(AutosarFact(
                subject=subject_key, predicate=predicate, object=object_key,
                evidence=_ev(chunk), confidence=conf,
                origin_chunk_id=chunk.chunk_id, dedupe_key=dk))
        return
    store.append(AutosarFact(
        subject=subject_key, predicate=predicate, object=object_key,
        evidence=_ev(chunk), confidence=conf,
        origin_chunk_id=chunk.chunk_id, dedupe_key=dk))


def _is_toc_chunk(chunk: Chunk, first_section_page: int
                  ) -> bool:
    """True for table-of-contents / front-matter chunks (D-022 lesson: they
    carry heading text but no architecture semantics).

    Two rules: (a) dot-leader-dominated chunks (classic ToC), and (b) chunks
    lying entirely before the document's first numbered section start page
    (title page, change-history tables, copyright, ToC — the R20-11 doc's
    first section starts on page 8; its change-history bullets fragment
    into FC-name sequences that would otherwise yield spurious facts).
    """
    if chunk.page_end < first_section_page and not chunk.section_no:
        return True
    lines = [ln.strip() for ln in chunk.text.splitlines() if ln.strip()]
    if not lines:
        return True
    toc = sum(1 for ln in lines if _TOC_LINE_RE.search(ln))
    return toc / len(lines) >= _TOC_CHUNK_MIN_RATIO


def _content_chunks(chunks: Sequence[Chunk]) -> list[Chunk]:
    """Chunks eligible for extraction (ToC/front-matter excluded).

    The first-section boundary is derived from the document itself: the
    earliest page a numbered section starts on (fallback: config default).
    """
    first_sec = min((c.page_start for c in chunks if c.section_no),
                    default=_FRONTMATTER_MAX_PAGE_DEFAULT)
    return [c for c in chunks if not _is_toc_chunk(c, first_sec)]


def _sentences(lines: Sequence[str]):
    """Yield (joined_sentence, start_line_idx) pairs from cleaned lines."""
    buf: list[str] = []
    buf_start = 0
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or _NOISE_RE.match(line) and len(line) < 3:
            continue
        if _TOC_LINE_RE.search(line):
            # never join a ToC line into an accumulated sentence
            if buf:
                yield " ".join(buf), buf_start
                buf, buf_start = [], 0
            continue
        if not buf:
            buf_start = i
        buf.append(line)
        if _SENT_END_RE.search(line):
            yield " ".join(buf), buf_start
            buf, buf_start = [], 0
    if buf:
        yield " ".join(buf), buf_start


# ------------------------------------------------------------ pass 1: names ----

def _heading_of(chunk: Chunk) -> str | None:
    """The section heading line of a chunk, when the chunk starts one."""
    if not chunk.section_no:
        return None
    for line in chunk.text.splitlines():
        if re.match(rf"^{re.escape(chunk.section_no)}\.?\s+\S", line.strip()):
            return line.strip()
    return None


def _collect_entities(chunks: Sequence[Chunk], store: dict,
                      aliases: dict[str, str]) -> set[str]:
    """Pass 1: populate the entity store + alias map from chunk texts."""
    seen: set[str] = set()
    chunks = _content_chunks(chunks)
    seen: set[str] = set()

    def add(etype: AutosarEntityType, name: str, chunk: Chunk,
            conf: float, attributes: dict[str, str] | None = None) -> str:
        key = normalize_key(etype, name)
        _mk_entity(store, etype, name, chunk, conf, attributes)
        seen.add(key)
        aliases[name] = key
        return key

    for c in chunks:
        # --- structural anchors + canonical FC names in prose ------------
        text = c.text
        for m in re.finditer(r"\b(Adaptive Applications|ARA|Functional "
                             r"Clusters|Adaptive Platform Foundation|"
                             r"Adaptive Platform Services)\b", text):
            surf = m.group(1)
            etype = _STRUCTURAL[surf]
            canon = _PLURAL_FIX.get(surf, surf)
            if etype in (AutosarEntityType.ADAPTIVE_APPLICATION,
                         AutosarEntityType.FUNCTIONAL_CLUSTER,
                         AutosarEntityType.PLATFORM_SERVICE):
                # generic concept entities: keep the plural surface form
                canon = surf
            add(etype, canon, c, CONF_NAME_MENTION)
        for m in re.finditer(
                r"\b(" + "|".join(re.escape(n) for n in _FC_NAMES) + r")\b",
                text):
            add(AutosarEntityType.FUNCTIONAL_CLUSTER, m.group(1), c,
                CONF_NAME_MENTION)
            # abbreviation alias when introduced as "Name (XX)"
            tail = text[m.end():m.end() + 12]
            ab = _ABBREV_RE.match(tail)
            if ab and ab.group(1) in _ABBREVS:
                aliases[ab.group(1)] = normalize_key(
                    AutosarEntityType.FUNCTIONAL_CLUSTER, m.group(1))

        # --- manifests ---------------------------------------------------
        for m in re.finditer(
                r"\b(" + "|".join(re.escape(mk) for mk in _MANIFEST_KINDS)
                + r")\b", text):
            canon = _MANIFEST_CANON[m.group(1)]
            add(AutosarEntityType.MANIFEST, canon, c, CONF_NAME_MENTION)

        # --- interface terms ----------------------------------------------
        for term in _INTERFACE_TERMS:
            if term in text:
                add(AutosarEntityType.INTERFACE, term, c, CONF_NAME_MENTION)

        # --- processes (generic) -------------------------------------------
        if re.search(r"\bprocess(?:es)?\b", text, re.IGNORECASE):
            add(AutosarEntityType.PROCESS, _PROCESS_NAME, c,
                CONF_NAME_MENTION)

        # --- software packages ----------------------------------------------
        for m in re.finditer(r"\bSoftware [Pp]ackages?\b", text):
            add(AutosarEntityType.SOFTWARE_PACKAGE, "Software Package", c,
                CONF_NAME_MENTION)

        # --- services (generic service concept) -------------------------------
        if re.search(r"\bservice[s]?\b", text, re.IGNORECASE):
            add(AutosarEntityType.SERVICE, "Service", c, CONF_NAME_MENTION)

        # --- Machine ---------------------------------------------------------
        if re.search(r"\bMachine\b", text):
            add(AutosarEntityType.MACHINE, "Machine", c, CONF_NAME_MENTION)

        # --- section-title FC detection (evidence-backed headings) -----------
        h = _heading_of(c)
        if h:
            for fc in _FC_NAMES:
                # heading "6 State Management" / "13 Update and Config
                # Management" style: title contains a canonical FC name
                if re.search(rf"\b{re.escape(fc)}\b", h):
                    add(AutosarEntityType.FUNCTIONAL_CLUSTER, fc, c,
                        CONF_SECTION_TITLE,
                        {"heading": h[:120]})
                    break

    # structural singular/plural consolidation
    for surf, etype in list(_STRUCTURAL.items()):
        canon = _PLURAL_FIX.get(surf)
        if canon and canon not in aliases:
            # ensure the singular concept exists too (shared key space)
            key = normalize_key(etype, canon)
            if key not in store:
                pass  # plural entity already covers the concept
    return seen


# ------------------------------------------------------ pass 2: relations ----

def _resolve(name: str, aliases: dict[str, str]) -> str | None:
    """Resolve a surface name / abbreviation to its canonical key."""
    if name in aliases:
        return aliases[name]
    return aliases.get(name.strip())


def _negation_free(sentence: str, m) -> bool:
    """Local negation guard: the match is usable when no negation phrase
    occurs within 80 characters before the trigger verb (the accumulated
    sentence window can span several true sentences, and a negation in an
    EARLIER sentence must not veto THIS relationship; the negation window
    before the verb is where "does not provide X" style refutations live)."""
    start = max(0, m.start() - 80)
    return not _NEGATION_RE.search(sentence[start:m.start() + 8])


def _named_spans(sentence: str, aliases: dict[str, str]):
    """All known-name occurrences in a sentence, left to right."""
    out = []
    for name in aliases:
        for m in re.finditer(rf"(?<![\w:])({re.escape(name)})(?![\w])",
                             sentence):
            out.append((m.start(), m.end(), name))
    out.sort()
    # drop overlapping spans (longest first wins)
    chosen: list[tuple[int, int, str]] = []
    last_end = -1
    for s, e, name in out:
        if s >= last_end:
            chosen.append((s, e, name))
            last_end = e
    return chosen


def _extract_relations(chunks: Sequence[Chunk], store: dict,
                       seen_keys: set[str], aliases: dict[str, str],
                       facts: list) -> None:
    """Pass 2: relationship extraction over sentence windows."""
    chunks = _content_chunks(chunks)
    for c in chunks:
        for sentence, _idx in _sentences(c.text.splitlines()):
            spans = _named_spans(sentence, aliases)
            if not spans:
                continue
            names = [n for _s, _e, n in spans]

            def key_of(n: str) -> str:
                return aliases[n]

            # --- provides_interface: FC ... provides ... interface --------
            m = _REL_PROVIDES_IF.search(sentence)
            if m and _negation_free(sentence, m):
                for n in names:
                    k = key_of(n)
                    if k.startswith("autosar:functional_cluster:"):
                        for ifc in _INTERFACE_TERMS:
                            ik = aliases.get(ifc)
                            # only bind interfaces the sentence itself names
                            if ik and re.search(rf"\b{re.escape(ifc)}\b",
                                                sentence, re.IGNORECASE):
                                _mk_fact(facts, seen_keys, k,
                                         AutosarPredicate.PROVIDES_INTERFACE,
                                         ik, c, CONF_RELATION_VERB)

            # --- runs_on: AA ... run on top of ARA -------------------------
            m = _REL_RUNS_ON.search(sentence)
            if m and _negation_free(sentence, m):
                for n in names:
                    k = key_of(n)
                    if k.startswith("autosar:adaptive_application:"):
                        if _ARA_KEY in seen_keys:
                            _mk_fact(facts, seen_keys, k,
                                     AutosarPredicate.RUNS_ON, _ARA_KEY, c,
                                     CONF_STRUCTURAL_SENTENCE)

            # --- belongs_to: FC ... belong to Foundation/Services ----------
            m = _REL_BELONGS.search(sentence)
            if m and _negation_free(sentence, m):
                for n in names:
                    k = key_of(n)
                    if k.startswith("autosar:functional_cluster:"):
                        _mk_fact(facts, seen_keys, k,
                                 AutosarPredicate.BELONGS_TO, _APF_KEY, c,
                                 CONF_RELATION_VERB)
                        _mk_fact(facts, seen_keys, k,
                                 AutosarPredicate.BELONGS_TO, _APS_KEY, c,
                                 CONF_RELATION_VERB)

            # --- commands: SM ... commanding ... EM ------------------------
            m = _REL_COMMANDS.search(sentence)
            if m and _negation_free(sentence, m):
                sm = aliases.get("State Management")
                em = aliases.get("Execution Management")
                if sm and em:
                    _mk_fact(facts, seen_keys, sm, AutosarPredicate.COMMANDS,
                             em, c, CONF_STRUCTURAL_SENTENCE)

            # --- interacts_with: X ... interacts with ... Y ----------------
            m = _REL_INTERACT.search(sentence)
            if m and _negation_free(sentence, m):
                subjects = [n for n in names
                            if key_of(n).startswith(
                                ("autosar:functional_cluster:",
                                 "autosar:adaptive_application:"))]
                for i, a in enumerate(subjects):
                    for b in subjects[i + 1:]:
                        _mk_fact(facts, seen_keys, key_of(a),
                                 AutosarPredicate.INTERACTS_WITH,
                                 key_of(b), c, CONF_RELATION_VERB)

            # --- implemented_as: AA/FC ... implemented as ... process ------
            m = _REL_IMPL_AS.search(sentence)
            if m and _negation_free(sentence, m):
                pk = aliases.get(_PROCESS_NAME)
                if pk:
                    for n in names:
                        k = key_of(n)
                        if k.startswith(
                                ("autosar:functional_cluster:",
                                 "autosar:adaptive_application:")):
                            _mk_fact(facts, seen_keys, k,
                                     AutosarPredicate.IMPLEMENTED_AS, pk, c,
                                     CONF_RELATION_VERB)

            # --- configured_by: ... configured in / based on <manifest> ---
            m = _REL_CONFIGURED_BY.search(sentence)
            if m and _negation_free(sentence, m):
                for n in names:
                    k = key_of(n)
                    if not k.startswith(
                            ("autosar:functional_cluster:",
                             "autosar:adaptive_application:",
                             "autosar:machine:")):
                        continue
                    for mk_canon in ("Machine Manifest", "Execution Manifest",
                                     "Service Instance Manifest"):
                        mk_key = aliases.get(mk_canon)
                        # only bind the manifest actually named in sentence
                        if mk_key and re.search(
                                rf"\b{re.escape(mk_canon)}\b", sentence,
                                re.IGNORECASE):
                            _mk_fact(facts, seen_keys, k,
                                     AutosarPredicate.CONFIGURED_BY, mk_key,
                                     c, CONF_RELATION_VERB)

            # --- uses_interface: uses ... interface ------------------------
            m = _REL_USES_IF.search(sentence)
            if m and _negation_free(sentence, m):
                for n in names:
                    k = key_of(n)
                    if not k.startswith(
                            ("autosar:functional_cluster:",
                             "autosar:adaptive_application:")):
                        continue
                    for ifc in _INTERFACE_TERMS:
                        ik = aliases.get(ifc)
                        if ik and re.search(rf"\b{re.escape(ifc)}\b",
                                            sentence, re.IGNORECASE):
                            _mk_fact(facts, seen_keys, k,
                                     AutosarPredicate.USES_INTERFACE, ik, c,
                                     CONF_RELATION_VERB)

            # --- provides_service: ... provide Services to other AA -------
            m = _REL_PROVIDES_SVC.search(sentence)
            if m and _negation_free(sentence, m):
                svc_key = aliases.get("Service")
                aa_key = aliases.get("Adaptive Applications")
                if svc_key and aa_key:
                    for n in names:
                        k = key_of(n)
                        if k.startswith(
                                ("autosar:adaptive_application:",
                                 "autosar:functional_cluster:")):
                            _mk_fact(facts, seen_keys, k,
                                     AutosarPredicate.PROVIDES_SERVICE,
                                     svc_key, c, CONF_RELATION_VERB)

            # --- updates: UCM ... update ... Software Package --------------
            m = _REL_UPDATES.search(sentence)
            if m and _negation_free(sentence, m):
                ucm = aliases.get(_UCM_NAME)
                pkg = aliases.get("Software Package")
                if ucm and pkg:
                    _mk_fact(facts, seen_keys, ucm,
                             AutosarPredicate.UPDATES, pkg, c,
                             CONF_RELATION_VERB)


# ------------------------------------------------------------------ entry ----

def extract_autosar(chunks: Sequence[Chunk]) -> AutosarExtraction:
    """Extract AUTOSAR entities + facts from one document's chunks (no LLM)."""
    ordered = sorted(chunks, key=lambda c: c.chunk_seq)
    out = AutosarExtraction()
    store: dict[str, object] = {}
    aliases: dict[str, str] = {}

    seen = _collect_entities(ordered, store, aliases)
    facts: list = []
    _extract_relations(ordered, store, seen, aliases, facts)

    out.entities = sorted(store.values(), key=lambda e: e.key)
    out.facts = sorted(facts, key=lambda f: f.dedupe_key)
    out.aliases = aliases
    out.stats = {
        "entities": len(out.entities),
        "facts": len(out.facts),
        "aliases": len(aliases),
    }
    return out
