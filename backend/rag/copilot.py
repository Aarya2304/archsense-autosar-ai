"""RAGCopilot (M3.12): the complete grounded question-answering flow.

    question -> hybrid retrieval -> evidence gate -> context builder
             -> LLM (structured JSON) -> mechanical citation validation
             -> CopilotAnswer

Every outcome is one of exactly four structured states (no exceptions leak
to callers, provider failures degrade to structured failures):

- ``answered``           : grounded answer with >= 1 validated citation;
- ``insufficient_evidence`` : gate or LLM refused; answer left empty;
- ``provider_failure``   : LLM/network error; retrieval metadata kept;
- ``validation_failure`` : LLM produced unparseable or uncited output.

Design constraints honored here: retrieval ranking happens BEFORE the LLM;
citation metadata comes only from trusted chunks; the LLM never becomes the
source of truth for provenance; nothing is fabricated on failure paths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from backend.rag.citations import (Citation, ParsedResponse, ValidationResult,
                                   parse_structured_response,
                                   validate_response, render_citations_block)
from backend.rag.context import BuiltContext, build_context
from backend.rag.gate import GateDecision, assess_evidence
from backend.rag.llm.base import LLMError, LLMProvider, LLMResponse
from backend.rag.models import RetrievalResult
from backend.rag.retriever import RetrievalService


@dataclass(frozen=True)
class CopilotAnswer:
    """Structured, caller-facing result of one ``ask()`` call."""

    status: str                       # answered | insufficient_evidence |
                                      # provider_failure | validation_failure
    question: str
    answer: str = ""                  # "" unless status == answered
    citations: list[Citation] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    llm: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    detail: str = ""                  # human-readable failure explanation

    @property
    def sources_block(self) -> str:
        """Deterministic rendered source list ("" when no citations)."""
        return render_citations_block(self.citations) if self.citations else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "question": self.question,
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "issues": self.issues,
            "gate": self.gate,
            "retrieval": self.retrieval,
            "llm": self.llm,
            "context": self.context,
            "timings_ms": {k: round(v, 1) for k, v in self.timings_ms.items()},
            "detail": self.detail,
        }


@dataclass
class RAGCopilot:
    """Coordinates retrieval, grounding, generation and validation.

    Dependency-injected: any ``LLMProvider`` (mock/OpenRouter/Ollama) and
    any service exposing ``retrieve`` (``HybridRetrievalService`` or the M2
    ``RetrievalService``) can be supplied. Gate thresholds default from
    ``backend.config`` (D-019 calibration values).
    """

    retriever: Any                    # HybridRetrievalService (or compatible)
    llm: LLMProvider
    top_k: int = 5
    max_evidence: int = 6
    min_score: float | None = None        # None -> backend.config default
    min_hits: int | None = None
    min_agreement: float | None = None
    min_lexical_score: float | None = None
    min_query_coverage: float | None = None
    # When True (default), the top-1 chunk is force-cited even if the model
    # omitted it; the validator still resolves it against trusted metadata.
    # Kept configurable because it is a policy choice, not a mechanical fact.
    force_top1_citation: bool = True

    def __post_init__(self) -> None:
        from backend.config import (EVIDENCE_MIN_AGREEMENT,
                                    EVIDENCE_MIN_HITS, EVIDENCE_MIN_SCORE,
                                    EVIDENCE_MIN_COVERAGE,
                                    EVIDENCE_MIN_LEXICAL_SCORE)

        self.min_score = (self.min_score if self.min_score is not None
                          else EVIDENCE_MIN_SCORE)
        self.min_hits = (self.min_hits if self.min_hits is not None
                         else EVIDENCE_MIN_HITS)
        self.min_agreement = (self.min_agreement
                              if self.min_agreement is not None
                              else EVIDENCE_MIN_AGREEMENT)
        self.min_lexical_score = (self.min_lexical_score
                                  if self.min_lexical_score is not None
                                  else EVIDENCE_MIN_LEXICAL_SCORE)
        self.min_query_coverage = (self.min_query_coverage
                                   if self.min_query_coverage is not None
                                   else EVIDENCE_MIN_COVERAGE)

    # ---------------------------------------------------------------- ask --
    def ask(self, question: str, top_k: int | None = None,
            filters: dict[str, Any] | None = None) -> CopilotAnswer:
        """Answer ``question`` through the full grounded pipeline."""
        t_total = time.perf_counter()
        k = top_k if top_k is not None else self.top_k
        question = question.strip()
        if not question:
            return CopilotAnswer(status="validation_failure",
                                 question=question, detail="empty question")

        # 1. Retrieval (ranked before any LLM involvement).
        t0 = time.perf_counter()
        retrieval = self.retriever.retrieve(question, top_k=k,
                                            filters=filters)
        t_retrieval = (time.perf_counter() - t0) * 1000.0
        retrieval_meta = {
            "mode": getattr(retrieval, "mode", "dense"),
            "top_k": k,
            "filters": dict(filters or {}),
            "n_chunks": len(retrieval.chunks),
            "chunk_ids": [c.chunk_id for c in retrieval.chunks],
        }

        # 2. Evidence gate (pre-generation refusal path).
        lexical = getattr(self.retriever, "_lexical", None)
        gate = assess_evidence(retrieval, min_score=self.min_score,
                               min_hits=self.min_hits,
                               min_agreement=self.min_agreement,
                               min_lexical_score=self.min_lexical_score,
                               min_query_coverage=self.min_query_coverage,
                               lexical=lexical)
        if not gate.passed:
            return self._finalize(CopilotAnswer(
                status="insufficient_evidence", question=question,
                gate=gate.to_dict(), retrieval=retrieval_meta,
                detail=f"evidence gate refused: {gate.reason}"),
                t_total, t_retrieval)

        # 3. Context builder.
        t0 = time.perf_counter()
        context = build_context(question, retrieval.chunks,
                                max_evidence=self.max_evidence)
        t_context = (time.perf_counter() - t0) * 1000.0

        # 4. LLM generation (structured JSON contract).
        t0 = time.perf_counter()
        try:
            llm_response = self.llm.generate(context.system_prompt,
                                             context.user_prompt)
        except LLMError as exc:
            return self._finalize(CopilotAnswer(
                status="provider_failure", question=question,
                gate=gate.to_dict(), retrieval=retrieval_meta,
                detail=f"LLM provider failed: {exc}"),
                t_total, t_retrieval, t_context=t_context)
        t_llm = (time.perf_counter() - t0) * 1000.0
        llm_meta = self._llm_meta(llm_response)

        # 5. Mechanical citation validation.
        parsed = parse_structured_response(llm_response.text)
        if parsed.declared_refusal:
            return self._finalize(CopilotAnswer(
                status="insufficient_evidence", question=question,
                gate=gate.to_dict(), retrieval=retrieval_meta, llm=llm_meta,
                context=context.to_dict(),
                detail="LLM judged evidence insufficient"),
                t_total, t_retrieval, t_context, t_llm)

        validation = validate_response(parsed, context)
        if not validation.ok:
            return self._finalize(CopilotAnswer(
                status="validation_failure", question=question,
                gate=gate.to_dict(), retrieval=retrieval_meta, llm=llm_meta,
                context=context.to_dict(),
                issues=[i.to_dict() for i in validation.issues],
                detail="citation validation rejected the response"),
                t_total, t_retrieval, t_context, t_llm)

        if self.force_top1_citation and context.blocks:
            top_block = context.blocks[0]
            if all(c.evidence_id != top_block.evidence_id
                   for c in validation.citations):
                validation.citations.append(
                    _citation_for(top_block, validation.answer))

        return self._finalize(CopilotAnswer(
            status="answered", question=question, answer=validation.answer,
            citations=validation.citations,
            issues=[i.to_dict() for i in validation.issues],
            gate=gate.to_dict(), retrieval=retrieval_meta, llm=llm_meta,
            context=context.to_dict()),
            t_total, t_retrieval, t_context, t_llm)

    # ------------------------------------------------------------ helpers --
    @staticmethod
    def _llm_meta(response: LLMResponse) -> dict[str, Any]:
        return {
            "provider": response.provider,
            "model": response.model,
            "latency_ms": round(response.latency_ms, 1),
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "finish_reason": response.finish_reason,
        }

    @staticmethod
    def _finalize(answer: CopilotAnswer, t_total: float,
                  t_retrieval: float, t_context: float = 0.0,
                  t_llm: float = 0.0) -> CopilotAnswer:
        """Attach timings immutably (frozen dataclass -> replace)."""
        timings = {
            "retrieval_ms": t_retrieval,
            "context_ms": t_context,
            "llm_ms": t_llm,
            "total_ms": (time.perf_counter() - t_total) * 1000.0,
        }
        return replace(answer, timings_ms=timings)


def _citation_for(block, answer: str) -> Citation:
    """Trusted-metadata citation for a context block (top-1 force path)."""
    return Citation(
        evidence_id=block.evidence_id,
        chunk_id=block.chunk_id,
        document_name=block.document_name,
        version=block.version,
        section_no=block.section_no,
        section_title=block.section_title,
        page_start=block.page_start,
        page_end=block.page_end,
        quote="",
        char_span=None,
    )
