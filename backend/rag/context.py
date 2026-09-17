"""Grounded context builder (M3.7): retrieved chunks -> LLM prompt context.

Deterministic, testable formatting between retrieval and generation:

- every retrieved chunk becomes one evidence block with a short internal
  evidence ID (``E1``, ``E2``, ...) assigned in retrieval rank order;
- every block carries full provenance (document, version, section, pages,
  chunk id) so the LLM can *see* where statements come from;
- the system prompt hard-codes the grounding contract: answer only from
  supplied evidence, cite evidence IDs, refuse when insufficient, never
  invent document/page/section identifiers.

Evidence IDs are the citation currency (M3.8): they are short (LLM-friendly),
mechanically resolvable (the validator owns the ID -> chunk mapping), and
never derived from LLM output. The mapping from evidence ID to chunk ID is
created here and consumed by the citation validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.rag.models import RetrievedChunk

GROUNDING_SYSTEM_PROMPT = """\
You are ArchSense, a careful automotive engineering assistant for AUTOSAR \
HLD documents.

Strict rules you must always follow:
1. Answer ONLY using the numbered evidence blocks supplied in the user \
message. Do not use outside knowledge, however plausible.
2. For every factual statement, cite the evidence block(s) that support it \
using their evidence IDs (e.g. [E1], [E2]) inline next to the statement.
3. If the evidence does not contain the answer, you MUST set \
"insufficient_evidence": true and leave "answer" empty. Do not guess.
4. Never invent evidence IDs, document names, page numbers or section \
numbers. Only cite IDs that appear in the supplied evidence.
5. Answer in precise, terse engineering prose. Quote identifiers (component \
names like C-08, interface IDs like IF-13, signal names) exactly as they \
appear in the evidence.

Respond with ONLY a JSON object, no markdown fences, in this exact shape:
{
  "answer": "<engineering answer with [En] citations>",
  "evidence_ids": ["E1", "E2"],
  "insufficient_evidence": false
}

The "evidence_ids" array must list every evidence block you used, and no \
others. If "insufficient_evidence" is true, "answer" must be "" and \
"evidence_ids" must be [].
"""

# Short user-prompt preamble before the evidence blocks.
_PROMPT_HEADER = "Answer the engineer's question using only this evidence.\n"
_PROMPT_FOOTER = ("\nQuestion: {question}\n"
                  "Respond with the JSON object exactly as specified.")


@dataclass(frozen=True)
class EvidenceBlock:
    """One evidence unit handed to the LLM (and later cited)."""

    evidence_id: str                 # "E1", "E2", ... (prompt-local)
    chunk_id: str                    # global deterministic chunk ID
    document_name: str
    version: str
    section_no: str
    section_title: str
    page_start: int
    page_end: int
    chunk_type: str
    score: float                     # retrieval similarity (display/gate)
    text: str

    @property
    def pages_csv(self) -> str:
        lo, hi = self.page_start, self.page_end
        return str(lo) if lo == hi else f"{lo}-{hi}"

    def header_lines(self) -> str:
        sec = f"{self.section_no} {self.section_title}".strip() or "(front matter)"
        return (f"Document: {self.document_name}\n"
                f"Version: {self.version}\n"
                f"Section: {sec}\n"
                f"Pages: {self.pages_csv}\n"
                f"Type: {self.chunk_type}")

    def render(self, max_chars: int | None = None) -> str:
        """The full block as it appears in the prompt.

        Blocks are labelled ``[EVIDENCE E<n>]`` — the explicit word
        "EVIDENCE" makes the ID unambiguous for the model and gives the
        offline mock provider a stable anchor line.
        """
        text = self.text
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + " ..."
        return (f"[EVIDENCE {self.evidence_id}]\n"
                f"{self.header_lines()}\n"
                f"Text:\n{text}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "chunk_id": self.chunk_id,
            "document_name": self.document_name,
            "version": self.version,
            "section_no": self.section_no,
            "section_title": self.section_title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "chunk_type": self.chunk_type,
            "score": round(self.score, 4),
            "text": self.text,
        }


@dataclass
class BuiltContext:
    """Everything the generator and validator need from the context step."""

    question: str
    system_prompt: str
    user_prompt: str
    blocks: list[EvidenceBlock] = field(default_factory=list)

    @property
    def evidence_map(self) -> dict[str, EvidenceBlock]:
        """evidence ID -> block (validator's trusted mapping)."""
        return {b.evidence_id: b for b in self.blocks}

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "n_blocks": len(self.blocks),
            "blocks": [b.to_dict() for b in self.blocks],
        }


def build_context(question: str, chunks: list[RetrievedChunk],
                  max_evidence: int = 6,
                  max_block_chars: int = 900,
                  system_prompt: str = GROUNDING_SYSTEM_PROMPT) -> BuiltContext:
    """Convert ranked retrieved chunks into the grounded prompt context.

    Deterministic: evidence IDs follow retrieval rank order (E1 = best).
    ``max_evidence`` caps prompt size; ``max_block_chars`` truncates the
    rare over-long chunk text (truncation is marked with " ...").
    """
    blocks: list[EvidenceBlock] = []
    for i, c in enumerate(chunks[:max_evidence], start=1):
        blocks.append(EvidenceBlock(
            evidence_id=f"E{i}",
            chunk_id=c.chunk_id,
            document_name=c.document_name,
            version=c.version,
            section_no=c.section_no,
            section_title=c.section_title,
            page_start=c.page_start,
            page_end=c.page_end,
            chunk_type=c.chunk_type,
            score=c.similarity,
            text=c.text,
        ))

    parts = [_PROMPT_HEADER]
    for b in blocks:
        parts.append(b.render(max_chars=max_block_chars))
        parts.append("")  # blank line between blocks
    parts.append(_PROMPT_FOOTER.format(question=question.strip()))
    user_prompt = "\n".join(parts)

    return BuiltContext(question=question, system_prompt=system_prompt,
                        user_prompt=user_prompt, blocks=blocks)
