"""Extraction context (M4.5/M4.6): chunks -> evidence blocks + trusted map.

Reuses the M3 evidence-ID philosophy: every input chunk becomes one evidence
block with a short internal ID (``E1``, ``E2``, ... in input order); the
application owns the ID -> provenance mapping and the LLM may only reference
IDs. The LLM never supplies document/version/section/page/chunk metadata —
provenance is resolved mechanically from this map (D-022).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.extraction.models import Source
from backend.rag.models import Chunk

_EXTRACTION_SYSTEM_PROMPT = """\
You are ArchSense's architecture extraction engine for AUTOSAR HLD documents.

Your task: read the numbered evidence blocks and extract software
architecture entities (components, interfaces, ports, signals, dependencies,
functional flows) and relationships between them.

Strict rules:
1. Extract ONLY what the evidence text explicitly states. Never infer,
   guess, or use outside knowledge.
2. For every entity or fact you output, set "evidence_id" to the ID of the
   evidence block it came from (e.g. "E1"). Never invent evidence IDs.
3. Never output document names, page numbers, section numbers or chunk IDs —
   the application resolves provenance from the evidence ID itself.
4. Use identifiers exactly as written in the evidence (C-08, IF-13, SG-018,
   DoorStatusIF, P-001).
5. If a block contains no extractable architecture content, skip it.

Respond with ONLY a JSON object in this exact shape:
{
  "entities": [
    {"type": "component", "name": "C-08", "evidence_id": "E1"}
  ],
  "facts": [
    {"subject": "component:C-08", "predicate": "provides",
     "object": "interface:IF-02", "evidence_id": "E1"}
  ]
}

Allowed entity types: component, interface, port, signal, dependency,
functional_flow.
Allowed predicates: provides, requires, depends_on, carries, implements,
participates_in.
Subject/object must be "<entity_type>:<identifier>" keys.
"""

_PROMPT_HEADER = "Extract architecture entities and facts from this evidence.\n"
_PROMPT_FOOTER = "\nRespond with the JSON object exactly as specified."


@dataclass(frozen=True)
class ExtractionEvidence:
    """One evidence unit handed to the extraction LLM (M4.6)."""

    evidence_id: str                    # "E1", "E2", ... (context-local)
    chunk_id: str                       # trusted global chunk ID
    source: Source                      # trusted provenance (never from LLM)
    text: str

    def render(self, max_chars: int | None = None) -> str:
        s = self.source
        text = self.text
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + " ..."
        return (f"[EVIDENCE {self.evidence_id}]\n"
                f"Document: {s.document_name} (v{s.version})\n"
                f"Section: {s.section_no} {s.section_title}\n"
                f"Pages: {s.pages_csv}\n"
                f"Text:\n{text}").rstrip()


@dataclass
class ExtractionContext:
    """Evidence blocks + the trusted evidence-ID -> Source map (M4.6)."""

    blocks: list[ExtractionEvidence] = field(default_factory=list)

    @property
    def evidence_map(self) -> dict[str, Source]:
        """evidence ID -> trusted provenance (validator's mapping)."""
        return {b.evidence_id: b.source for b in self.blocks}

    @property
    def system_prompt(self) -> str:
        return _EXTRACTION_SYSTEM_PROMPT

    def user_prompt(self, max_block_chars: int = 1200) -> str:
        parts = [_PROMPT_HEADER]
        for b in self.blocks:
            parts.append(b.render(max_chars=max_block_chars))
            parts.append("")
        parts.append(_PROMPT_FOOTER)
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "n_blocks": len(self.blocks),
            "blocks": [
                {"evidence_id": b.evidence_id, "chunk_id": b.chunk_id,
                 **b.source.to_dict()}
                for b in self.blocks
            ],
        }


def build_extraction_context(chunks: list[Chunk]) -> ExtractionContext:
    """Convert provenance-carrying chunks into the extraction context.

    Deterministic: evidence IDs follow input order (E1 = first chunk).
    """
    blocks = [
        ExtractionEvidence(
            evidence_id=f"E{i}",
            chunk_id=c.chunk_id,
            source=Source(
                document_name=c.document_name,
                version=c.version,
                sha256=c.sha256,
                section_no=c.section_no,
                section_title=c.section_title,
                page_start=c.page_start,
                page_end=c.page_end,
                chunk_id=c.chunk_id,
            ),
            text=c.text,
        )
        for i, c in enumerate(chunks, start=1)
    ]
    return ExtractionContext(blocks=blocks)
