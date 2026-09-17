"""ExtractionService (M4.12): the high-level extraction orchestration.

    chunks (M2, provenance-carrying)
        -> deterministic extractor
        -> optional LLM extraction (M3 provider, evidence-ID based)
        -> mechanical validation (evidence, references, confidence, dedupe)
        -> SQLite registry (typed tables + extraction_facts) + audit

Keeps components independently testable: each stage is a separate module
and the service only coordinates. Fully offline by default (mock LLM).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.extraction import deterministic as det
from backend.extraction import llm as llm_mod
from backend.extraction import registry
from backend.extraction.context import build_extraction_context
from backend.extraction.models import (ExtractedEntity, ExtractedFact,
                                       Source)
from backend.extraction.validator import (ExtractionIssue, ValidatedExtraction,
                                          validate_extraction)
from backend.rag.llm.base import LLMProvider
from backend.rag.models import Chunk

DEFAULT_MIN_CONFIDENCE = 0.0   # accept everything validated; floor is config


@dataclass
class ExtractionRunResult:
    """Structured result of one extraction run (CLI/UI/report surface)."""

    status: str                       # "completed" | "completed_with_issues"
    document_name: str
    version: str
    run_id: int | None = None         # AnalysisRun.id (None when not persisting)
    entities: list[ExtractedEntity] = field(default_factory=list)
    facts: list[ExtractedFact] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        by_type: dict[str, int] = {}
        for e in {e.key: e for e in self.entities}.values():
            by_type[e.entity_type.value] = by_type.get(
                e.entity_type.value, 0) + 1
        by_pred: dict[str, int] = {}
        for f in self.facts:
            by_pred[f.predicate.value] = by_pred.get(f.predicate.value, 0) + 1
        lines = [
            f"Document : {self.document_name} (v{self.version})",
            f"Status   : {self.status}"
            + (f" ({len(self.issues)} issues)" if self.issues else ""),
        ]
        if self.run_id is not None:
            lines.append(f"Run      : analysis_run #{self.run_id}")
        lines.append(f"Entities : {len({e.key for e in self.entities})} unique "
                     f"({', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))})")
        lines.append(f"Facts     : {len(self.facts)} unique "
                     f"({', '.join(f'{k}={v}' for k, v in sorted(by_pred.items()))})")
        t = self.timings_ms
        lines.append(f"Timings  : det={t.get('deterministic_ms', 0):.0f}ms "
                     f"llm={t.get('llm_ms', 0):.0f}ms "
                     f"validate={t.get('validate_ms', 0):.0f}ms "
                     f"persist={t.get('persist_ms', 0):.0f}ms "
                     f"total={t.get('total_ms', 0):.0f}ms")
        return "\n".join(lines)


class ExtractionService:
    """Coordinates deterministic + optional LLM extraction (M4.12)."""

    def __init__(self, llm_provider: LLMProvider | None = None,
                 min_confidence: float = DEFAULT_MIN_CONFIDENCE,
                 use_llm: bool = False,
                 session_factory=None,
                 db_path=None) -> None:
        self.llm_provider = llm_provider
        self.min_confidence = min_confidence
        self.use_llm = use_llm
        self._session_factory = session_factory
        self._db_path = db_path          # None -> config DB_PATH (tests: tmp)

    # -------------------------------------------------------------- chunks --
    @staticmethod
    def _source_of(chunk: Chunk) -> Source:
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

    # --------------------------------------------------------------- main --
    def extract_from_chunks(self, chunks: Sequence[Chunk],
                            document_name: str = "",
                            version: str = "",
                            persist: bool = False,
                            reset: bool = False) -> ExtractionRunResult:
        """Full pipeline over provenance-carrying chunks (M4.12).

        ``persist=True`` writes the registry (creating a document/version
        row when the storage layer has none yet). ``reset=True`` clears the
        version's prior extraction output first (``--reset`` path).
        """
        t_total = time.perf_counter()
        ordered = sorted(chunks, key=lambda c: c.chunk_seq)
        doc_name = document_name or (ordered[0].document_name if ordered
                                     else "unknown")
        ver = version or (ordered[0].version if ordered else "unknown")

        # 1. deterministic extraction
        t0 = time.perf_counter()
        det_out = det.extract_deterministic(ordered)
        t_det = (time.perf_counter() - t0) * 1000.0

        # 2. optional LLM extraction (mock by default; real via provider)
        llm_candidates: list[ExtractedEntity | ExtractedFact] = []
        llm_issues: list[dict] = []
        llm_meta: dict = {}
        t_llm = 0.0
        if self.use_llm and self.llm_provider is not None:
            t0 = time.perf_counter()
            ctx = build_extraction_context(ordered)
            llm_res = llm_mod.run_llm_extraction(ctx, self.llm_provider)
            t_llm = (time.perf_counter() - t0) * 1000.0
            llm_candidates = [*llm_res.entities, *llm_res.facts]
            llm_issues = list(llm_res.issues)
            if not llm_res.parse_ok:
                # Provider/parse failures must SURFACE (audit-friendly),
                # never be silently swallowed — the deterministic output
                # still stands, but the run reports the LLM problem.
                llm_issues.append({"kind": "llm_parse_error",
                                   "detail": llm_res.parse_error})
            llm_meta = llm_res.llm_meta

        # 3. validation (evidence resolution, references, confidence, dedupe)
        t0 = time.perf_counter()
        candidates = [*det_out.entities, *det_out.facts, *llm_candidates]
        emap = self._emap_for(ordered)
        validated = validate_extraction(
            candidates, emap, aliases=det_out.aliases,
            min_confidence=self.min_confidence)
        t_val = (time.perf_counter() - t0) * 1000.0

        # 4. persistence
        t0 = time.perf_counter()
        run_id: int | None = None
        if persist:
            run_id, _ = self._persist(doc_name, ver, ordered, validated,
                                      reset, llm_meta)
        t_persist = (time.perf_counter() - t0) * 1000.0

        issues = [i.to_dict() for i in validated.issues] + llm_issues
        status = "completed" if not issues else "completed_with_issues"
        timings = {
            "deterministic_ms": round(t_det, 1),
            "llm_ms": round(t_llm, 1),
            "validate_ms": round(t_val, 1),
            "persist_ms": round(t_persist, 1),
            "total_ms": round((time.perf_counter() - t_total) * 1000.0, 1),
        }
        stats = {
            **det_out.stats,
            "validated": validated.stats.to_dict(),
            "llm": llm_meta,
        }
        return ExtractionRunResult(
            status=status, document_name=doc_name, version=ver,
            run_id=run_id, entities=validated.entities,
            facts=validated.facts, issues=issues, stats=stats,
            timings_ms=timings)

    # ------------------------------------------------------------ helpers --
    @staticmethod
    def _emap_for(chunks: Sequence[Chunk]) -> dict[str, Source]:
        """Evidence-ID -> Source map mirroring build_extraction_context."""
        return {f"E{i}": ExtractionService._source_of(c)
                for i, c in enumerate(sorted(chunks,
                                             key=lambda c: c.chunk_seq), 1)}

    def _persist(self, doc_name: str, ver: str, chunks: Sequence[Chunk],
                 validated: ValidatedExtraction, reset: bool,
                 llm_meta: dict) -> tuple[int | None, dict]:
        """Persist the registry, creating project/doc/version rows once."""
        from backend.config import DB_PATH
        from backend.storage.database import (get_session, init_schema,
                                              make_engine,
                                              make_session_factory)
        from backend.storage.models import Document, DocumentVersion, Project

        engine = make_engine(self._db_path or DB_PATH)
        init_schema(engine)
        factory = make_session_factory(engine)
        session = factory()
        try:
            project = session.query(Project).filter_by(
                name="default").first()
            if project is None:
                project = Project(name="default", description="ArchSense MVP")
                session.add(project)
                session.commit()
            doc = session.query(Document).filter_by(
                project_id=project.id, filename=doc_name).first()
            if doc is None:
                doc = Document(project_id=project.id, filename=doc_name,
                               title=doc_name, doc_type="HLD",
                               sha256=(chunks[0].sha256 if chunks else ""))
                session.add(doc)
                session.commit()
                session.refresh(doc)
            dv = session.query(DocumentVersion).filter_by(
                document_id=doc.id, version_label=ver).first()
            if dv is None:
                dv = DocumentVersion(
                    document_id=doc.id, version_label=ver,
                    status="ready",
                    page_count=(max(c.page_end for c in chunks)
                                if chunks else 0),
                    chunk_count=len(chunks))
                session.add(dv)
                session.commit()
                session.refresh(dv)
            version_id = dv.id

            if reset:
                registry.clear_registry(session, version_id)

            run = registry.begin_run(session, version_id,
                                     params={"use_llm": self.use_llm,
                                             "min_confidence":
                                             self.min_confidence,
                                             "llm": llm_meta})
            counts = registry.persist_extraction(
                session, version_id,
                validated.entities, validated.facts)
            registry.finish_run(session, run, {
                **counts, "issues": len(validated.issues)})
            return run.id, counts
        finally:
            session.close()
