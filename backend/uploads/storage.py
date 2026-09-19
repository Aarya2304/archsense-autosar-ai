"""User-upload storage (M9): safe filenames, hashes, dedupe, registration.

Security posture (task Part R):

- PDF extension check + content sniffing (``%PDF-`` magic bytes) — a renamed
  ``.txt`` is rejected before PyMuPDF ever opens it.
- Filename sanitization: only ``[A-Za-z0-9._ -]`` survive; path separators
  (``/``, ``\\``), ``..`` and control characters are stripped/underscores.
  The stored name is additionally prefixed with a short SHA-256 digest so two
  different uploads with the same surface name cannot collide on disk.
- Hard size cap (``backend.config.MAX_UPLOAD_MB``, default 100 MB) — enforced
  on the byte stream BEFORE any parsing work.
- Content identity = SHA-256: identical bytes are never ingested twice; the
  earlier registration is returned with ``duplicate=True``.
- Nothing here executes uploaded content; PDFs are only *read* by PyMuPDF
  inside the existing M1 parser.

Deduplication is per-project (the M1 ``documents`` table has a unique
(project_id, sha256) constraint — we reuse it, D-004 schema, no new tables).
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import MAX_UPLOAD_MB, UPLOADS_DIR
from backend.ingestion import pdf_parser
from backend.storage.models import Document, DocumentVersion, Project

_PDF_MAGIC = b"%PDF-"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
_NAME_PARTS_RE = re.compile(r"[A-Za-z0-9._ -]{1,120}")


class UploadError(ValueError):
    """A user-actionable upload rejection (bad type, too large, bad PDF)."""


@dataclass
class StoredUpload:
    """Result of storing one uploaded file (pre-ingestion)."""

    stored_path: Path
    original_name: str
    safe_name: str
    sha256: str
    size_bytes: int
    duplicate: bool = False          # same bytes already registered
    document_id: int | None = None   # M1 Document row (set at registration)
    version_id: int | None = None
    version_label: str = "unversioned"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "original_name": self.original_name,
            "safe_name": self.safe_name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "duplicate": self.duplicate,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "version_label": self.version_label,
            "notes": list(self.notes),
        }


def sanitize_filename(name: str) -> str:
    """Reduce an uploaded filename to a safe single path component.

    - strips any directory parts the client may have sent (both separators),
    - removes control chars and anything outside ``[A-Za-z0-9._ -]``,
    - collapses dots so the result can never be ``..`` / hidden traversal,
    - caps length (keeping the ``.pdf`` extension).
    """
    name = (name or "upload.pdf").replace("\\", "/").split("/")[-1]
    name = name.strip()
    cleaned = _SAFE_NAME_RE.sub("_", name).strip(". ")
    if not cleaned:
        cleaned = "upload.pdf"
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    stem, ext = cleaned[:-4], cleaned[-4:]
    # keep the stem traversal-free (".." was already stripped by strip('.'))
    if len(stem) > 100:
        stem = stem[:100]
    return f"{stem}{ext}"


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project(session: Session) -> Project:
    proj = session.execute(
        select(Project).where(Project.name == "default")).scalars().first()
    if proj is None:
        proj = Project(name="default", description="ArchSense MVP")
        session.add(proj)
        session.commit()
        session.refresh(proj)
    return proj


def find_by_sha(session: Session, sha256: str) -> Document | None:
    """Existing registration of identical content (per default project)."""
    proj = _project(session)
    return session.execute(
        select(Document).where(Document.project_id == proj.id,
                               Document.sha256 == sha256)
    ).scalars().first()


def store_upload(data: bytes, original_name: str,
                 session: Session | None = None) -> StoredUpload:
    """Validate + persist one uploaded PDF; register/check content identity.

    ``session`` may be supplied (tests / batch pipeline); when omitted a
    short-lived session on the project DB is used.
    """
    original_name = original_name or "upload.pdf"
    safe = sanitize_filename(original_name)

    if not safe.lower().endswith(".pdf"):
        raise UploadError(f"Only PDF files are accepted (got {original_name!r}).")
    if len(data) == 0:
        raise UploadError(f"{original_name} is empty.")
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise UploadError(
            f"{original_name} is {len(data) / (1024 * 1024):.1f} MB — the "
            f"limit is {MAX_UPLOAD_MB} MB.")
    if not data[:len(_PDF_MAGIC)] == _PDF_MAGIC:
        raise UploadError(
            f"{original_name} does not look like a PDF (missing %PDF- "
            f"header). Renamed non-PDF files are rejected.")

    sha = compute_sha256(data)
    digest8 = sha[:8]

    uploads_dir = UPLOADS_DIR
    uploads_dir.mkdir(parents=True, exist_ok=True)
    # digest prefix prevents same-name-different-content collisions on disk
    stored_name = f"{digest8}_{safe}"
    stored_path = uploads_dir / stored_name

    own = session is None
    if own:
        from backend.storage.database import (get_session, init_schema,
                                              make_engine,
                                              make_session_factory)
        from backend.config import DB_PATH
        engine = make_engine(DB_PATH)
        init_schema(engine)
        session = get_session(make_session_factory(engine))

    try:
        dup = find_by_sha(session, sha)
        if dup is not None:
            prior = dup_sha_path(session, dup)
            up = StoredUpload(
                stored_path=(Path(prior) if prior else stored_path),
                original_name=original_name, safe_name=safe, sha256=sha,
                size_bytes=len(data), duplicate=True,
                document_id=dup.id,
                notes=["identical content already registered"])
            # ensure the file exists in the uploads area for preview
            if not up.stored_path.is_file():
                up.stored_path = stored_path
                stored_path.write_bytes(data)
            return up

        stored_path.write_bytes(data)
        # verify PyMuPDF can open it and it has pages (fail EARLY, clean)
        try:
            doc = pdf_parser.open_document(stored_path)
            try:
                pages = doc.page_count
                if pages <= 0:
                    raise UploadError(f"{original_name} contains no pages.")
                meta = pdf_parser.document_metadata(doc)
            finally:
                doc.close()
        except UploadError:
            stored_path.unlink(missing_ok=True)
            raise
        except Exception as exc:  # malformed PDF -> clean rejection
            stored_path.unlink(missing_ok=True)
            raise UploadError(
                f"{original_name} could not be parsed as a PDF: "
                f"{type(exc).__name__}: {exc}") from exc

        up = StoredUpload(
            stored_path=stored_path, original_name=original_name,
            safe_name=safe, sha256=sha, size_bytes=len(data),
            duplicate=False, notes=[f"pages: {pages}"])
        up.notes.append(
            f"pdf metadata title: {meta.get('title') or '(none)'}")
        return up
    finally:
        if own:
            session.close()


def dup_sha_path(session: Session, doc: Document) -> str | None:
    """Best-effort path of an already-registered document (preview reuse)."""
    return None  # registration path resolved by the pipeline, not storage


def register_document(session: Session, up: StoredUpload,
                      version_label: str | None = None,
                      title: str | None = None,
                      doc_type: str = "HLD") -> Document:
    """Create (or fetch) the M1 ``Document`` row for an upload.

    The version label is the caller's decision (profile/version detection);
    storage only persists what it is told (D-047: no invented versions).
    """
    proj = _project(session)
    doc = find_by_sha(session, up.sha256)
    if doc is not None:
        up.document_id = doc.id
        return doc
    doc = Document(project_id=proj.id, filename=up.safe_name,
                   title=title or up.safe_name, doc_type=doc_type,
                   sha256=up.sha256)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    up.document_id = doc.id
    return doc


def register_version(session: Session, doc: Document,
                     version_label: str, page_count: int,
                     chunk_count: int = 0,
                     stats: dict | None = None) -> DocumentVersion:
    """Create (or update) the ``DocumentVersion`` row for an upload."""
    dv = session.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == doc.id,
            DocumentVersion.version_label == version_label)
    ).scalars().first()
    if dv is None:
        dv = DocumentVersion(document_id=doc.id, version_label=version_label,
                             status="processing")
        session.add(dv)
    dv.page_count = page_count
    dv.chunk_count = chunk_count
    dv.stats_json = {**(dv.stats_json or {}), **(stats or {})}
    dv.status = "ready"
    session.commit()
    session.refresh(dv)
    return dv
