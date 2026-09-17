"""LLM-assisted extraction (M4.5/M4.13/M4.14) via the M3 provider contract.

The extraction LLM receives the deterministic built context (evidence
blocks with trusted provenance) and must answer with the structured JSON
contract from ``context._EXTRACTION_SYSTEM_PROMPT``. This module:

- builds candidates from the LLM's JSON (typed, via the Pydantic schema);
- resolves ``evidence_id`` references against the trusted map — the LLM
  NEVER supplies document/version/section/page/chunk metadata (D-022);
- collects malformed responses into explicit issues (never silent
  acceptance): unparseable JSON, invalid entity types, invalid predicates,
  unknown evidence IDs, confidence violations.

``MockExtractionProvider`` is a deterministic offline stand-in implementing
the same ``LLMProvider`` protocol subset used here (``generate``), so the
full LLM extraction path is testable without credentials. OpenRouter and
Ollama work unchanged through ``get_llm_provider`` (M3 factory) — the
extraction layer only ever calls ``provider.generate(system, user)``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.extraction.context import ExtractionContext
from backend.extraction.models import (EntityType, EvidenceRef, ExtractedEntity,
                                       ExtractedFact, Predicate)
from backend.rag.llm.base import LLMError, LLMProvider, LLMResponse, UsageInfo

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

_KNOWN_TYPES = {t.value for t in EntityType}
_KNOWN_PREDICATES = {p.value for p in Predicate}


@dataclass
class LLMExtractionResult:
    """Outcome of one LLM extraction call (structured, audit-friendly)."""

    entities: list[ExtractedEntity] = field(default_factory=list)
    facts: list[ExtractedFact] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    llm_meta: dict = field(default_factory=dict)
    parse_ok: bool = True
    parse_error: str = ""

    @property
    def ok(self) -> bool:
        return self.parse_ok and not self.issues


# ------------------------------------------------------------------ parsing ----

def _extract_json(text: str) -> dict | None:
    """First balanced JSON object (fence/prose tolerant, like M3)."""
    cleaned = re.sub(r"```(?:json)?", "", text)
    m = _JSON_OBJECT_RE.search(cleaned)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_llm_extraction(response: LLMResponse,
                         ctx: ExtractionContext) -> LLMExtractionResult:
    """Turn a provider response into validated-shape candidates (M4.5).

    Evidence references are resolved here against the trusted map; unknown
    IDs produce issues (M4.6) — provenance metadata always comes from the
    map, never from the LLM.
    """
    emap = ctx.evidence_map
    out = LLMExtractionResult(
        llm_meta={"provider": response.provider, "model": response.model,
                  "latency_ms": round(response.latency_ms, 1),
                  "prompt_tokens": response.usage.prompt_tokens,
                  "completion_tokens": response.usage.completion_tokens})

    obj = _extract_json(response.text)
    if obj is None:
        out.parse_ok = False
        out.parse_error = "no parseable JSON object in LLM response"
        return out

    def _ev_for(raw_eid) -> EvidenceRef | None:
        eid = str(raw_eid).strip() if raw_eid else ""
        if eid not in emap:
            out.issues.append({
                "kind": "unknown_evidence_id",
                "detail": f"LLM referenced {eid or '(empty)'} which is not "
                          f"in the evidence map"})
            return None
        return EvidenceRef(evidence_id=eid)

    for i, ent in enumerate(obj.get("entities", []) or []):
        if not isinstance(ent, dict):
            out.issues.append({"kind": "construction_error",
                               "detail": f"entity #{i} is not an object"})
            continue
        etype = str(ent.get("type", "")).strip().lower()
        if etype not in _KNOWN_TYPES:
            out.issues.append({"kind": "invalid_entity_type",
                               "detail": f"entity #{i}: unknown type "
                                         f"{etype!r}"})
            continue
        ev = _ev_for(ent.get("evidence_id"))
        if ev is None:
            continue
        try:
            out.entities.append(ExtractedEntity(
                entity_type=EntityType(etype),
                name=str(ent.get("name", "")),
                attributes={str(k): str(v) for k, v in
                            (ent.get("attributes") or {}).items()},
                evidence=ev,
                confidence=float(ent.get("confidence", 0.75)),
                extractor="llm"))
        except (ValueError, TypeError) as exc:
            out.issues.append({"kind": "construction_error",
                               "detail": f"entity #{i}: {exc}"})

    for i, fct in enumerate(obj.get("facts", []) or []):
        if not isinstance(fct, dict):
            out.issues.append({"kind": "construction_error",
                               "detail": f"fact #{i} is not an object"})
            continue
        pred = str(fct.get("predicate", "")).strip().lower()
        if pred not in _KNOWN_PREDICATES:
            out.issues.append({"kind": "invalid_predicate",
                               "detail": f"fact #{i}: unknown predicate "
                                         f"{pred!r}"})
            continue
        ev = _ev_for(fct.get("evidence_id"))
        if ev is None:
            continue
        try:
            out.facts.append(ExtractedFact(
                subject=str(fct.get("subject", "")),
                predicate=Predicate(pred),
                object=str(fct.get("object", "")),
                object_value=str(fct.get("object_value", "")),
                evidence=ev,
                confidence=float(fct.get("confidence", 0.75)),
                extractor="llm"))
        except (ValueError, TypeError) as exc:
            out.issues.append({"kind": "construction_error",
                               "detail": f"fact #{i}: {exc}"})

    return out


# ----------------------------------------------------------------- service ----

def run_llm_extraction(ctx: ExtractionContext, provider: LLMProvider,
                       max_block_chars: int = 1200) -> LLMExtractionResult:
    """One LLM extraction call over the context (M4.5).

    Provider failures degrade to a structured result (parse_ok=False with
    the error recorded) — never an exception leak into the pipeline.
    """
    t0 = time.perf_counter()
    try:
        response = provider.generate(ctx.system_prompt,
                                     ctx.user_prompt(max_block_chars))
    except LLMError as exc:
        return LLMExtractionResult(
            parse_ok=False, parse_error=f"provider failure: {exc}",
            llm_meta={"provider": getattr(provider, "name", "?"),
                      "model": getattr(provider, "model_name", "?"),
                      "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
    return parse_llm_extraction(response, ctx)


# -------------------------------------------------------------------- mock ----

class MockExtractionProvider:
    """Deterministic offline extraction provider (M4.13).

    Implements the same generate() contract as M3's MockLLMProvider but for
    the extraction prompt: it re-emits entities/facts it can *see* in the
    evidence text using the same deterministic patterns as the offline
    extractor, so the LLM code path is exercised end-to-end without
    credentials. Supports failure/refusal injection for tests.
    """

    def __init__(self, fail_with: str | None = None,
                 malformed: bool = False,
                 cite_unknown_evidence: bool = False) -> None:
        self.fail_with = fail_with
        self.malformed = malformed
        self.cite_unknown_evidence = cite_unknown_evidence
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return "mock-extraction-deterministic"

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        t0 = time.perf_counter()
        self.calls.append({"system": system_prompt, "user": user_prompt})
        if self.fail_with:
            raise LLMError(self.fail_with)

        if self.malformed:
            text = "this is definitely not json"
        else:
            eid = "E999" if self.cite_unknown_evidence else None
            payload = self._extract_from_prompt(user_prompt, eid)
            text = json.dumps(payload, indent=2)

        usage = UsageInfo(prompt_tokens=len(system_prompt + user_prompt) // 4,
                          completion_tokens=len(text) // 4,
                          total_tokens=(len(system_prompt) + len(user_prompt)
                                        + len(text)) // 4)
        return LLMResponse(text=text, provider=self.name,
                           model=self.model_name,
                           latency_ms=(time.perf_counter() - t0) * 1000,
                           usage=usage, finish_reason="stop")

    # ------------------------------------------------------------ helpers --
    @staticmethod
    def _extract_from_prompt(user_prompt: str,
                             force_eid: str | None) -> dict:
        """Deterministic extraction from the rendered evidence blocks."""
        from backend.extraction.deterministic import (
            _COMP_TITLE_RE, _IFACE_TITLE_RE, _PROVIDER_RE, _CONSUMERS_RE,
            _CONSUMER_CELL_RE)
        from backend.rag.lexical import tokenize  # noqa: F401 (parity import)

        entities: list[dict] = []
        facts: list[dict] = []
        blocks = re.split(r"\n(?=\[EVIDENCE E\d+\])", user_prompt)
        for block in blocks:
            m = re.match(r"\[EVIDENCE (E\d+)\]", block)
            if not m:
                continue
            eid = force_eid or m.group(1)
            for cm in _COMP_TITLE_RE.finditer(block):
                entities.append({"type": "component",
                                 "name": cm.group(1),
                                 "attributes": {"id": cm.group(1).upper(),
                                                "name": cm.group(2)},
                                 "evidence_id": eid})
            for im in _IFACE_TITLE_RE.finditer(block):
                entities.append({"type": "interface",
                                 "name": im.group(2).upper(),
                                 "attributes": {"id": im.group(2).upper(),
                                                "name": im.group(1)},
                                 "evidence_id": eid})
                region = block[im.end():]
                prov = _PROVIDER_RE.search(region)
                if prov:
                    facts.append({
                        "subject": f"component:{prov.group(1).upper()}",
                        "predicate": "provides",
                        "object": f"interface:{im.group(2).upper()}",
                        "evidence_id": eid})
                cons = _CONSUMERS_RE.search(region)
                if cons:
                    for cell in _CONSUMER_CELL_RE.finditer(cons.group(1)):
                        facts.append({
                            "subject": f"component:{cell.group(1).upper()}",
                            "predicate": "requires",
                            "object": f"interface:{im.group(2).upper()}",
                            "evidence_id": eid})
        return {"entities": entities, "facts": facts}
