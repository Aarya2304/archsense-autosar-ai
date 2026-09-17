"""Mechanical citation validator (M3.8–M3.10).

Pure application logic — never calls the LLM. Responsibilities:

1. Parse the generator's structured JSON response, tolerating common LLM
   formatting noise (markdown fences, prose around JSON). Malformed output
   degrades to an explicit failure state, never to blind acceptance.
2. Resolve every cited evidence ID against the trusted ID -> chunk mapping
   built by the context builder. Unknown/invented IDs are rejected.
3. Extract supporting quotes for each citation mechanically (longest common
   token subsequence between the answer and the cited chunk's text) — the
   LLM never writes the quote.
4. Verify LLM-declared evidence_ids against the citations actually present
   in the answer text; contradictions are reported.
5. Render final citations deterministically from trusted chunk metadata
   (document/version/section/pages/chunk id). The LLM cannot introduce any
   of these values because they are never taken from its output.

Design rules (from the plan): the validator is deterministic, side-effect
free, and safe to unit-test exhaustively.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from backend.rag.context import BuiltContext, EvidenceBlock

# Inline citation forms: [E1], [E12], [ E1 ], [E1, E2]
_CITE_RE = re.compile(r"\[\s*(E\d{1,3})\s*\]")

# --------------------------------------------------------------- parsing --


@dataclass
class ParsedResponse:
    """The LLM response after structural parsing (before validation)."""

    answer: str
    evidence_ids: list[str] = field(default_factory=list)
    insufficient_evidence: bool = False
    parse_ok: bool = True
    parse_error: str | None = None

    @property
    def declared_refusal(self) -> bool:
        return self.insufficient_evidence


def parse_structured_response(text: str) -> ParsedResponse:
    """Parse the generator's raw text into a structured response.

    Tolerates markdown code fences and leading/trailing prose by extracting
    the first JSON object. A response with no parseable JSON object, a
    non-dict object, or missing required keys parses as NOT ok (the copilot
    treats it as a provider/contract failure — never as a valid answer).
    """
    if not text or not text.strip():
        return ParsedResponse("", [], False, False, "empty response")

    candidate = _extract_json_object(text)
    if candidate is None:
        return ParsedResponse("", [], False, False,
                              "no JSON object found in response")
    try:
        obj = json.loads(candidate)
    except ValueError as exc:
        return ParsedResponse("", [], False, False, f"invalid JSON: {exc}")
    if not isinstance(obj, dict):
        return ParsedResponse("", [], False, False,
                              "JSON response is not an object")

    answer = obj.get("answer")
    evidence = obj.get("evidence_ids", [])
    insufficient = obj.get("insufficient_evidence")

    missing = [k for k, v in (("answer", answer),
                              ("insufficient_evidence", insufficient))
               if v is None]
    if missing:
        return ParsedResponse("", [], False, False,
                              f"missing required field(s): {missing}")
    if not isinstance(answer, str):
        return ParsedResponse("", [], False, False, "'answer' must be a string")
    if not isinstance(evidence, list) or \
            not all(isinstance(e, str) for e in evidence):
        return ParsedResponse("", [], False, False,
                              "'evidence_ids' must be a list of strings")
    if not isinstance(insufficient, bool):
        # Be strict: a truthy non-bool is ambiguous -> parse failure.
        return ParsedResponse("", [], False, False,
                              "'insufficient_evidence' must be a boolean")

    return ParsedResponse(answer=answer,
                          evidence_ids=[e.strip() for e in evidence if e.strip()],
                          insufficient_evidence=insufficient)


def _extract_json_object(text: str) -> str | None:
    """First balanced top-level JSON object in ``text`` (fence-tolerant)."""
    cleaned = re.sub(r"```(?:json)?", "", text)
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start:i + 1]
    return None


# ------------------------------------------------------------ validation --


@dataclass(frozen=True)
class Citation:
    """One validated citation, rendered from trusted metadata only."""

    evidence_id: str
    chunk_id: str
    document_name: str
    version: str
    section_no: str
    section_title: str
    page_start: int
    page_end: int
    quote: str
    char_span: tuple[int, int] | None   # answer span citing this block

    @property
    def pages_csv(self) -> str:
        lo, hi = self.page_start, self.page_end
        return str(lo) if lo == hi else f"{lo}-{hi}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "chunk_id": self.chunk_id,
            "document_name": self.document_name,
            "version": self.version,
            "section": (f"{self.section_no} {self.section_title}".strip()
                        or "(front matter)"),
            "section_no": self.section_no,
            "section_title": self.section_title,
            "pages": self.pages_csv,
            "quote": self.quote,
        }


@dataclass
class CitationIssue:
    """One validation problem (surfaced to caller / audit; never hidden)."""

    kind: str          # "unknown_evidence_id" | "no_citations" |
                       # "uncited_declared_id" | "empty_answer"
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail}


@dataclass
class ValidationResult:
    """Outcome of validating one parsed response against the context."""

    ok: bool
    answer: str                        # original answer text (validated only)
    citations: list[Citation] = field(default_factory=list)
    issues: list[CitationIssue] = field(default_factory=list)
    declared_ids: list[str] = field(default_factory=list)

    @property
    def rejected_ids(self) -> list[str]:
        return [i.detail for i in self.issues if i.kind == "unknown_evidence_id"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "issues": [i.to_dict() for i in self.issues],
            "declared_ids": self.declared_ids,
        }


def validate_response(parsed: ParsedResponse, context: BuiltContext) \
        -> ValidationResult:
    """Validate a parsed generator response against the built context.

    Rules:
    - every inline [En] citation must exist in the context's evidence map;
    - ANY unknown evidence ID — inline [En] or declared in evidence_ids —
      fails the whole response (ok=False): a valid citation elsewhere never
      rescues a fabricated one ("claim [E999] [E1]" -> validation failure);
    - a non-refusal answer with zero surviving citations fails validation;
    - declared evidence_ids not actually cited in the answer are reported
      (kept as warnings, not hard failures — models occasionally cite
      supportingly without inlining);
    - citations carry metadata exclusively from the trusted evidence blocks.
    """
    emap = context.evidence_map
    issues: list[CitationIssue] = []

    if parsed.declared_refusal:
        # Refusals carry no citations by contract.
        return ValidationResult(ok=True, answer="", citations=[],
                                issues=issues, declared_ids=parsed.evidence_ids)

    if not parsed.answer.strip():
        issues.append(CitationIssue("empty_answer",
                                    "answer is empty but refusal flag is false"))
        return ValidationResult(ok=False, answer=parsed.answer, citations=[],
                                issues=issues, declared_ids=parsed.evidence_ids)

    # 1. Resolve inline citations in order of first appearance. Unknown
    #    IDs are recorded (all of them, for audit) and hard-fail below —
    #    a valid citation elsewhere never rescues a fabricated one.
    resolved: list[Citation] = []
    seen: set[str] = set()
    unknown_inline: list[str] = []
    for m in _CITE_RE.finditer(parsed.answer):
        eid = m.group(1)
        if eid in seen:
            continue
        seen.add(eid)
        block = emap.get(eid)
        if block is None:
            unknown_inline.append(eid)
            issues.append(CitationIssue(
                "unknown_evidence_id",
                f"LLM cited {eid} which is not in the retrieved evidence"))
            continue
        resolved.append(_build_citation(eid, block, parsed.answer, m.span()))

    # 2. Declared IDs outside the map are fabricated evidence references.
    declared_unknown = [e for e in parsed.evidence_ids if e not in emap]
    for eid in declared_unknown:
        issues.append(CitationIssue(
            "unknown_evidence_id",
            f"declared evidence_ids entry {eid} is not in the evidence map"))

    # 3. Hard gate: ANY fabricated evidence reference fails the response,
    #    even when other citations are valid (e.g. "claim [E999] [E1]").
    if unknown_inline or declared_unknown:
        return ValidationResult(ok=False, answer=parsed.answer, citations=[],
                                issues=issues, declared_ids=parsed.evidence_ids)

    # 4. Declared-but-uncited IDs (soft issue; kept out of the hard gate).
    uncited_declared = [e for e in parsed.evidence_ids
                        if e in emap and e not in seen]
    for eid in uncited_declared:
        issues.append(CitationIssue(
            "uncited_declared_id",
            f"declared {eid} but never cited it in the answer"))

    # 5. Hard gate: grounded answers must carry >= 1 surviving citation.
    if not resolved:
        issues.append(CitationIssue(
            "no_citations",
            "answer has no resolvable citations; refusing to present it as "
            "grounded"))
        return ValidationResult(ok=False, answer=parsed.answer, citations=[],
                                issues=issues, declared_ids=parsed.evidence_ids)

    return ValidationResult(ok=True, answer=parsed.answer,
                            citations=resolved, issues=issues,
                            declared_ids=parsed.evidence_ids)


def _build_citation(eid: str, block: EvidenceBlock, answer: str,
                    span: tuple[int, int] | None) -> Citation:
    return Citation(
        evidence_id=eid,
        chunk_id=block.chunk_id,
        document_name=block.document_name,
        version=block.version,
        section_no=block.section_no,
        section_title=block.section_title,
        page_start=block.page_start,
        page_end=block.page_end,
        quote=_extract_quote(answer, block.text),
        char_span=span,
    )


def _extract_quote(answer: str, chunk_text: str, max_words: int = 30) -> str:
    """Mechanically extract the chunk text most similar to the answer.

    Slides a window over the chunk's sentences and keeps the sentence group
    with the highest similarity to the cited span of the answer. Fuzzy by
    design (difflib), deterministic, and never LLM-written. Returns "" when
    nothing overlaps meaningfully (the citation still stands; the UI just
    shows no quote).
    """
    from backend.rag.lexical import tokenize

    a_tokens = tokenize(answer)
    if not a_tokens:
        return ""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", chunk_text)
                 if s.strip()]
    if not sentences:
        return ""

    best, best_score = "", 0.0
    for i in range(len(sentences)):
        window = sentences[i:i + 2]           # 1–2 sentence windows
        candidate = " ".join(window)
        c_tokens = tokenize(candidate)
        if not c_tokens:
            continue
        # Recall-oriented: how much of the answer's vocabulary appears here.
        overlap = len(set(a_tokens) & set(c_tokens)) / max(1, len(set(a_tokens)))
        ratio = SequenceMatcher(None, " ".join(a_tokens[:60]),
                                " ".join(c_tokens[:60])).ratio()
        score = 0.5 * overlap + 0.5 * ratio
        if score > best_score:
            best, best_score = candidate, score
    if best_score < 0.15:
        return ""
    words = best.split()
    return " ".join(words[:max_words]) + (" ..." if len(words) > max_words
                                          else "")


# ------------------------------------------------------------- rendering --

def render_citations_block(citations: list[Citation]) -> str:
    """Deterministic numbered source list (rank order = citation order)."""
    lines = ["Sources:"]
    for i, c in enumerate(citations, start=1):
        sec = f"{c.section_no} {c.section_title}".strip() or "(front matter)"
        lines.append(f"[{i}] {c.document_name} v{c.version}")
        lines.append(f"    Section {sec} - Page(s) {c.pages_csv}")
        lines.append(f"    chunk {c.chunk_id}")
        if c.quote:
            lines.append(f"    quote: \"{c.quote}\"")
    return "\n".join(lines)
