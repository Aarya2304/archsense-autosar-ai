"""Deterministic offline LLM provider (M3.4).

``MockLLMProvider`` implements the same ``LLMProvider`` contract without
any network or model: it reads the evidence blocks embedded in the user
prompt by the M3.7 context builder and synthesizes a valid structured JSON
response from them. This lets the whole citation-validation + refusal
pipeline be exercised end-to-end deterministically (tests, CI, and key-less
demo runs) while exercising the exact same downstream code as real
providers.

Behaviour:
- If the question looks answerable from the evidence (default), the mock
  answers with the most relevant evidence block(s) quoted + a composed
  sentence, and cites the corresponding evidence IDs.
- If ``fail_with`` is set, ``generate`` raises ``LLMError`` (provider-failure
  tests).
- If ``force_refusal`` is set, it emits the structured insufficient-evidence
  response (refusal-path tests).

Determinism: evidence selection is order-stable (context order + score
tie-break by evidence ID); no randomness anywhere.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from backend.rag.llm.base import LLMError, LLMResponse, UsageInfo

# Matches one evidence block produced by backend.rag.context.build_context.
_EVIDENCE_ID_RE = re.compile(r"^\[EVIDENCE (\S+)\]", re.MULTILINE)


def _split_blocks(user_prompt: str) -> list[tuple[str, str]]:
    """[(evidence_id, block_text)] in prompt order.

    The trailing ``Question:`` footer is cut first: otherwise it would be
    absorbed into the last evidence block and that block would trivially
    contain every question token (always ranking first).
    """
    footer = re.search(r"\nQuestion:", user_prompt)
    body = user_prompt[:footer.start()] if footer else user_prompt
    blocks: list[tuple[str, str]] = []
    matches = list(_EVIDENCE_ID_RE.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        blocks.append((m.group(1), body[m.start():end]))
    return blocks


@dataclass
class MockLLMProvider:
    """Deterministic evidence-extracting provider (no network, no model)."""

    model_name: str = "mock-deterministic"
    fail_with: str | None = field(default=None,
                                  metadata={"doc": "raise LLMError(msg)"})
    force_refusal: bool = False
    _calls: list[dict] = field(default_factory=list, init=False, repr=False)

    @property
    def name(self) -> str:
        return "mock"

    def generate(self, system_prompt: str, user_prompt: str) -> LLMResponse:
        t0 = time.perf_counter()
        self._calls.append({"system": system_prompt, "user": user_prompt})
        if self.fail_with:
            raise LLMError(self.fail_with)

        question = self._extract_question(user_prompt)
        blocks = _split_blocks(user_prompt)
        refusal = {"answer": "",
                   "evidence_ids": [],
                   "insufficient_evidence": True,
                   "missing": ["no evidence in context"]}
        if self.force_refusal or not blocks:
            payload = refusal
        else:
            ranked = self._rank_blocks(question, blocks)
            top_id, top_block = ranked[0]
            # Relevance heuristic standing in for real LLM judgment: refuse
            # only when the question shares essentially no vocabulary with
            # the best block (zero-topic questions). Deliberately weak —
            # measured on this corpus, NO token-overlap signal separates
            # topically-adjacent unanswerable questions (price, airbag) from
            # answerable ones; that judgment requires a real model reading
            # the evidence, which is exactly the layer this mock stands in
            # for (see docs/PROJECT_DECISIONS.md D-019).
            from backend.rag.lexical import tokenize

            q_toks = set(tokenize(question))
            b_toks = set(tokenize(top_block))
            overlap = (len(q_toks & b_toks) / len(q_toks)) if q_toks else 1.0
            if overlap < 0.2:
                payload = refusal
            else:
                answer = self._compose_answer(question,
                                              [b for _, b in ranked[:2]])
                # Inline citations: the validator requires them in the
                # answer text, exactly as the grounding contract demands
                # from real models.
                ids = [eid for eid, _ in ranked[:2]]
                answer = answer.rstrip() + "" + "".join(f" [{eid}]"
                                                         for eid in ids)
                payload = {
                    "answer": answer,
                    "evidence_ids": ids,
                    "insufficient_evidence": False,
                }
                if not payload["answer"].strip():
                    payload = refusal

        latency_ms = (time.perf_counter() - t0) * 1000.0
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        usage = UsageInfo(prompt_tokens=len(user_prompt) // 4,
                          completion_tokens=len(text) // 4,
                          total_tokens=(len(user_prompt) + len(text)) // 4)
        return LLMResponse(text=text, provider=self.name,
                           model=self.model_name, latency_ms=latency_ms,
                           usage=usage, finish_reason="stop")

    # ------------------------------------------------------------ helpers --
    @staticmethod
    def _extract_question(user_prompt: str) -> str:
        m = re.search(r"Question:\s*(.+)", user_prompt)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _rank_blocks(question: str,
                     blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Order-stable relevance: shared-token count, ties by prompt order."""
        from backend.rag.lexical import tokenize

        q_tokens = set(tokenize(question))
        scored: list[tuple[int, int, str, str]] = []
        for order, (eid, block) in enumerate(blocks):
            overlap = len(q_tokens & set(tokenize(block)))
            scored.append((-overlap, order, eid, block))
        scored.sort()
        return [(eid, block) for _, _, eid, block in scored]

    @staticmethod
    def _compose_answer(question: str, blocks: list[str]) -> str:
        """First two sentences of the best block, verbatim (no invention)."""
        head = re.sub(r"^\[EVIDENCE \S+\]\n?", "", blocks[0]).strip()
        # Strip the provenance header lines (Document:/Version:/Section:/Pages:)
        body_lines = [ln for ln in head.splitlines()
                      if not re.match(
                          r"^(Document|Version|Section|Pages|Type):", ln)]
        body = " ".join(ln.strip() for ln in body_lines if ln.strip())
        body = re.sub(r"^Text:\s*", "", body)
        sentences = re.split(r"(?<=[.!?])\s+", body)
        taken: list[str] = []
        for s in sentences:
            if not s.strip():
                continue
            taken.append(s.strip())
            if len(taken) == 2:
                break
        answer = " ".join(taken)
        if len(blocks) > 1:
            extra = re.sub(r"^\[EVIDENCE \S+\]\n?", "", blocks[1]).strip()
            extra_lines = [ln for ln in extra.splitlines()
                           if not re.match(
                               r"^(Document|Version|Section|Pages|Type):", ln)]
            extra_body = " ".join(ln.strip() for ln in extra_lines
                                  if ln.strip())
            extra_body = re.sub(r"^Text:\s*", "", extra_body)
            extra_sents = [s.strip() for s in
                           re.split(r"(?<=[.!?])\s+", extra_body) if s.strip()]
            if extra_sents:
                answer += " " + extra_sents[0]
        return answer.strip()
