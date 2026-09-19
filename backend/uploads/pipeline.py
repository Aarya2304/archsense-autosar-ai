"""Upload processing pipeline (M9): upload -> M1 -> M2 -> profile -> M4.

    bytes
      -> storage.store_upload          (validate, hash, dedupe, save)
      -> ingestion.ingest_pdf          (M1 page records; unchanged)
      -> processed JSON                (data/uploads/processed/)
      -> chunker + embedder + store    (M2; ISOLATED upload collection)
      -> profiles.detect_profile       (evidence-based)
      -> extraction (profile-gated):
           application_hld           -> existing M4 ExtractionService
           autosar_adaptive_platform -> M9 AUTOSAR extractor + validator
           generic                   -> none (honest unavailability)
      -> registration                  (Document/DocumentVersion rows)

Key properties (task Parts C/K/R):

- The MAIN corpus Chroma collection (data/vectors/chroma) is never touched:
  uploads index into the configured UPLOAD_COLLECTION in the same persist
  directory, so Copilot can retrieve uploaded documents without any risk of
  corrupting the M2/M3 regression index (D-047).
- Every stage is timed; the returned record carries per-stage milliseconds
  for the UI progress display and the M9 performance report.
- Registration persists document name, detected version (or the honest
  ``unversioned`` label), page count, SHA-256, section count and all
  stage stats into the version row's ``stats_json`` (M1 schema reuse).
- All pipeline failures raise ``UploadError`` with user-actionable text;
  partial artifacts are cleaned up when a stage fails irrecoverably.
- No LLM and no network anywhere in this module (deterministic + offline).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.config import (UPLOAD_COLLECTION, UPLOADS_PROCESSED_DIR)
from backend.ingestion.pipeline import ingest_pdf
from backend.uploads.profiles import (ProfileDetection, detect_profile,
                                      get_profile)
from backend.uploads.storage import (StoredUpload, UploadError,
                                     register_document, register_version,
                                     store_upload)


@dataclass
class UploadPipelineResult:
    """Everything the UI/CLI needs about one processed upload."""

    original_name: str
    safe_name: str
    sha256: str
    profile: str
    profile_label: str
    structured: bool
    version_label: str
    page_count: int
    chunk_count: int
    section_count: int
    entity_count: int = 0
    fact_count: int = 0
    issues: list[dict] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    stored_path: str = ""
    processed_json: str = ""
    collection: str = UPLOAD_COLLECTION
    duplicate: bool = False
    detection: dict = field(default_factory=dict)
    version_id: int | None = None
    document_id: int | None = None

    def summary(self) -> str:
        lines = [
            f"{self.original_name} -> {self.safe_name}",
            f"  profile : {self.profile_label} ({self.profile})"
            + ("  [MANUAL]" if self.detection.get("manual") else ""),
            f"  version : {self.version_label} | pages {self.page_count}"
            f" | chunks {self.chunk_count} | sections {self.section_count}",
            f"  sha256  : {self.sha256[:16]}...",
        ]
        if self.structured:
            lines.append(
                f"  M4       : {self.entity_count} entities, "
                f"{self.fact_count} facts")
        else:
            lines.append(
                "  M4       : structured extraction not available for the "
                "generic profile (M1/M2/M3 analysis only)")
        t = self.timings_ms
        lines.append(
            "  timings  : ingest={ingest_ms:.0f}ms chunk={chunk_ms:.0f}ms "
            "index={index_ms:.0f}ms extract={extract_ms:.0f}ms "
            "register={register_ms:.0f}ms total={total_ms:.0f}ms".format(
                **{**{k: 0.0 for k in ("ingest_ms", "chunk_ms", "index_ms",
                                       "extract_ms", "register_ms",
                                       "total_ms")}, **t}))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ok": True,
            "original_name": self.original_name,
            "safe_name": self.safe_name,
            "sha256": self.sha256,
            "profile": self.profile,
            "profile_label": self.profile_label,
            "structured": self.structured,
            "version_label": self.version_label,
            "page_count": self.page_count,
            "chunk_count": self.chunk_count,
            "section_count": self.section_count,
            "entity_count": self.entity_count,
            "fact_count": self.fact_count,
            "issues": list(self.issues),
            "timings_ms": dict(self.timings_ms),
            "stored_path": self.stored_path,
            "processed_json": self.processed_json,
            "collection": self.collection,
            "duplicate": self.duplicate,
            "detection": dict(self.detection),
            "version_id": self.version_id,
            "document_id": self.document_id,
        }


# ----------------------------------------------------------------- stages ----


def _store_upload_storage(data: bytes, original_name: str,
                          session) -> StoredUpload:
    return store_upload(data, original_name, session=session)


def _m1_ingest(stored_path: Path) -> tuple[object, float]:
    t0 = time.perf_counter()
    result = ingest_pdf(stored_path)
    return result, (time.perf_counter() - t0) * 1000.0


def _save_processed(result, safe_name: str) -> tuple[Path, float]:
    from backend.ingestion.pipeline import save_result

    t0 = time.perf_counter()
    out_dir = Path(UPLOADS_PROCESSED_DIR)
    out = out_dir / f"{Path(safe_name).stem}__processed.json"
    save_result(result, out)
    return out, (time.perf_counter() - t0) * 1000.0


def _m2_index(processed_json: Path, version_label: str,
              rebuild: bool = False) -> tuple[list, float, int]:
    """Chunk + embed + upsert into the ISOLATED upload collection."""
    from backend.config import VECTORS_DIR
    from backend.rag.embedder import get_embedder
    from backend.rag.indexing import index_processed_document
    from backend.rag.vector_store import get_vector_store

    t0 = time.perf_counter()
    store = get_vector_store(collection_name=UPLOAD_COLLECTION,
                             persist_dir=VECTORS_DIR / "chroma")
    embedder = get_embedder()
    chunks, _summary = index_processed_document(
        processed_json, embedder, store, rebuild=rebuild,
        version=version_label)
    return chunks, (time.perf_counter() - t0) * 1000.0, store.count()


def _guess_version(result) -> str:
    """Honest version label: explicit caller version > title-page heuristic
    > 'unversioned' (no invented versions, D-047)."""
    from backend.ingestion.metadata_extractor import guess_version

    title_text = "\n".join(pg.text for pg in result.pages[:3])
    guessed = guess_version(title_text)
    return guessed or "unversioned"


def _unique_version_label(candidate: str, sha256: str, session) -> str:
    """Globally-unique version label (M9).

    M0-M8 services resolve versions by label alone, but ``version_label`` is
    only unique per document in the M1 schema — two unversioned uploads
    would otherwise collide on ``unversioned`` and every label-based lookup
    (profile, graph, findings, compare gate) could resolve the WRONG
    document. Unrelated uploads keep distinct labels by appending the
    smallest free ``-N`` suffix; re-uploading identical content keeps its
    existing label (dedupe path never reaches here).
    """
    from sqlalchemy import select
    from backend.storage.models import Document, DocumentVersion

    taken = {
        (row.document.sha256, row.version_label)
        for row in session.execute(
            select(DocumentVersion).join(
                Document, DocumentVersion.document_id == Document.id)).scalars()
    }
    if (sha256, candidate) in taken:
        return candidate                     # same content re-registered
    if not any(lb == candidate for _sha, lb in taken):
        return candidate
    n = 2
    while f"{candidate}-{n}" in {lb for _sha, lb in taken}:
        n += 1
    return f"{candidate}-{n}"


def _profile_extraction(result, chunks: list, profile: str,
                        document_name: str, version_label: str,
                        session, version_id: int | None,
                        profile_detection: ProfileDetection,
                        reset: bool = False) -> tuple[int, int, list[dict], float]:
    """Profile-gated structured extraction. Returns (entities, facts,
    issues, elapsed_ms). Generic profile: honest no-op (0/0)."""
    t0 = time.perf_counter()
    entities = facts = 0
    issues: list[dict] = []

    if not get_profile(profile).structured:
        return 0, 0, issues, (time.perf_counter() - t0) * 1000.0

    if profile == "application_hld":
        # existing M4 service, unchanged
        from backend.extraction.service import ExtractionService

        svc = ExtractionService(session_factory=None)
        svc._db_path = None
        res = svc.extract_from_chunks(
            chunks, document_name=document_name, version=version_label,
            persist=True, reset=reset)
        # entity/fact counts from the validated run
        entities = len({e.key for e in res.entities})
        facts = len(res.facts)
        issues = list(res.issues)
    elif profile == "autosar_adaptive_platform":
        from backend.extraction.autosar import (begin_run, extract_autosar,
                                                finish_run, clear_registry,
                                                persist_extraction,
                                                validate_autosar_extraction)

        if reset and version_id is not None:
            clear_registry(session, version_id)
        out = extract_autosar(chunks)
        # evidence map: chunk order -> trusted Source (M4 convention E1..En)
        from backend.extraction.models import Source as M4Source
        from backend.extraction.service import ExtractionService

        ordered = sorted(chunks, key=lambda c: c.chunk_seq)
        emap = {f"E{i}": ExtractionService._source_of(c)
                for i, c in enumerate(ordered, 1)}
        # candidates carry direct sources already; validate mechanically
        candidates = [*out.entities, *out.facts]
        validated = validate_autosar_extraction(candidates, emap)
        issues = [i.to_dict() for i in validated.issues]
        if version_id is not None:
            run = begin_run(session, version_id,
                            params={"extractor": "deterministic"})
            counts = persist_extraction(session, version_id,
                                        validated.entities, validated.facts)
            finish_run(session, run, {**counts,
                                      "issues": len(validated.issues)})
            entities, facts = counts["entities"], counts["facts"]
        else:                                    # dry-run (tests/CLI)
            entities = len(validated.entities)
            facts = len(validated.facts)
    else:  # pragma: no cover - future profiles
        issues.append({"kind": "profile_unsupported",
                       "detail": f"profile {profile!r} has no extractor"})

    return entities, facts, issues, (time.perf_counter() - t0) * 1000.0


# ------------------------------------------------------------------ entry ----


def process_upload(data: bytes, original_name: str,
                   version: str | None = None,
                   manual_profile: str | None = None,
                   rebuild: bool = False,
                   reset: bool = False,
                   session=None) -> UploadPipelineResult:
    """Full upload pipeline for one PDF (explicit action; never auto-run).

    ``version`` overrides version detection; ``manual_profile`` forces the
    profile (recorded as manual). Raises ``UploadError`` with an
    actionable message on any validation/parsing failure.
    """
    own = session is None
    if own:
        from backend.storage.database import (get_session, init_schema,
                                              make_engine)
        from backend.config import DB_PATH

        engine = make_engine(DB_PATH)
        init_schema(engine)
        session = get_session(engine)

    t_total = time.perf_counter()
    timings: dict[str, float] = {}
    try:
        # 1. store + validate + dedupe -----------------------------------
        up = _store_upload_storage(data, original_name, session)
        if up.duplicate and not reset:
            # Re-report the earlier registration without re-processing.
            from sqlalchemy import select
            from backend.storage.models import Document, DocumentVersion

            doc = session.get(Document, up.document_id)
            dv = None
            if doc is not None:
                dv = session.execute(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == doc.id)
                    .order_by(DocumentVersion.id.desc())
                ).scalars().first()
            if dv is None:
                raise UploadError(
                    "Duplicate content detected but the prior registration "
                    "is missing; re-process with reset=True.")
            prior = (dv.stats_json or {}).get("upload") or {}
            prof = get_profile(prior.get("profile", "generic"))
            res = UploadPipelineResult(
                original_name=original_name,
                safe_name=up.safe_name, sha256=up.sha256,
                profile=prof.name, profile_label=prof.label,
                structured=prof.structured,
                version_label=dv.version_label,
                page_count=dv.page_count, chunk_count=dv.chunk_count,
                section_count=prior.get("section_count", 0),
                entity_count=prior.get("entity_count", 0),
                fact_count=prior.get("fact_count", 0),
                timings_ms=prior.get("timings_ms", {}),
                stored_path=str(up.stored_path),
                processed_json=prior.get("processed_json", ""),
                duplicate=True, detection=prior.get("detection", {}),
                version_id=dv.id, document_id=doc.id)
            res.issues = [{"kind": "duplicate",
                           "detail": "identical content already registered "
                                     "(nothing re-processed)"}]
            return res

        # 2. M1 ingestion --------------------------------------------------
        result, timings["ingest_ms"] = _m1_ingest(up.stored_path)

        # 3. processed JSON (uploads runtime area) --------------------------
        processed_json, timings["save_ms"] = _save_processed(
            result, up.safe_name)

        # 4. version detection (honest) ------------------------------------
        version_label = _unique_version_label(
            version or _guess_version(result), up.sha256, session)

        # 5. profile detection (evidence-based) ----------------------------
        pages_text = ["\n".join(pg.lines) for pg in result.pages]
        detection = detect_profile(pages_text, result.tables,
                                   result.document_name,
                                   manual=manual_profile)
        profile = detection.profile
        prof = get_profile(profile)

        # 6. M2 chunking + indexing (isolated collection) --------------------
        chunks, timings["index_ms"], _n = _m2_index(
            processed_json, version_label, rebuild=rebuild)
        t_chunk = sum(c.token_count for c in chunks)

        # 7. registration (M1 rows; stats persisted in stats_json) ----------
        t0 = time.perf_counter()
        # doc_type mirrors the detected profile so pre-M9 label-based
        # fallbacks (m9_services._PROFILE_BY_DOC_TYPE) stay honest for
        # uploaded documents too (ABC docs are HLD; the AUTOSAR profile maps
        # to its own type).
        doc_type = {"application_hld": "HLD",
                    "autosar_adaptive_platform": "AUTOSAR_AP"}.get(
            profile, "GENERIC")
        doc = register_document(session, up, title=result.metadata.get(
            "title") or up.safe_name, doc_type=doc_type)
        dv = register_version(
            session, doc, version_label, result.page_count,
            chunk_count=len(chunks),
            stats={"upload": {
                "original_name": original_name,
                "safe_name": up.safe_name,
                "section_count": len(result.sections),
                "detection": detection.to_dict(),
                "processed_json": str(processed_json),
                "collection": UPLOAD_COLLECTION,
            }})
        timings["register_ms"] = (time.perf_counter() - t0) * 1000.0

        # 8. profile-gated structured extraction ----------------------------
        entities, facts, issues, timings["extract_ms"] = _profile_extraction(
            result, chunks, profile, up.safe_name, version_label, session,
            dv.id, detection, reset=reset)

        timings["total_ms"] = (time.perf_counter() - t_total) * 1000.0
        timings.setdefault("chunk_ms", 0.0)

        # refresh version stats with extraction outcome ----------------------
        dv.stats_json = {**(dv.stats_json or {}), **{
            "upload": {
                **(dv.stats_json or {}).get("upload", {}),
                "entity_count": entities,
                "fact_count": facts,
                "timings_ms": {k: round(v, 1) for k, v in timings.items()},
                "issues": issues[:20],
                "token_count": t_chunk,
            }}}
        dv.status = "ready"
        session.commit()

        return UploadPipelineResult(
            original_name=original_name,
            safe_name=up.safe_name,
            sha256=up.sha256,
            profile=profile,
            profile_label=prof.label,
            structured=prof.structured,
            version_label=version_label,
            page_count=result.page_count,
            chunk_count=len(chunks),
            section_count=len(result.sections),
            entity_count=entities,
            fact_count=facts,
            issues=issues,
            timings_ms={k: round(v, 1) for k, v in timings.items()},
            stored_path=str(up.stored_path),
            processed_json=str(processed_json),
            duplicate=False,
            detection=detection.to_dict(),
            version_id=dv.id,
            document_id=doc.id,
        )
    except UploadError:
        raise
    except Exception as exc:  # surface as clean actionable error
        msg = str(exc)
        if "produced no chunks" in msg:
            # the M1 cleaner dropped every line (e.g. a scan-like page with
            # almost no extractable text) -> tell the user what to do
            raise UploadError(
                f"Processing failed for {original_name}: no indexable text "
                "was found (the PDF may be a scan or contain only images; "
                "try a text-based PDF).") from exc
        raise UploadError(
            f"Processing failed for {original_name}: "
            f"{type(exc).__name__}: {exc}") from exc
    finally:
        if own:
            session.close()


def process_upload_file(path: Path, **kwargs) -> UploadPipelineResult:
    """Convenience wrapper for CLI/tests: read the file and process."""
    path = Path(path)
    if not path.is_file():
        raise UploadError(f"file not found: {path}")
    data = path.read_bytes()
    return process_upload(data, path.name, **kwargs)
