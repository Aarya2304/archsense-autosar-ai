"""ORM models for the ArchSense SQLite schema (M1 scope).

Schema overview (see docs/PROJECT_DECISIONS.md Part 10):
projects -> documents -> document_versions -> pages/chunks/entities/findings.
``audit_events`` is append-only and written by every consequential action.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Enum, Float, ForeignKey,
                        Index, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.storage.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ enums ----


class DocStatus(str, enum.Enum):
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class EntityStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class FindingStatus(str, enum.Enum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_DISCUSSION = "needs_discussion"


class FindingSeverity(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingOrigin(str, enum.Enum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"


# ------------------------------------------------------------------ tables ---


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    documents: Mapped[list["Document"]] = relationship(back_populates="project",
                                                       cascade="all, delete")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    filename: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="")
    doc_type: Mapped[str] = mapped_column(String(50), default="HLD")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    active_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id"), nullable=True)

    project: Mapped[Project] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete",
        foreign_keys="DocumentVersion.document_id")

    __table_args__ = (
        UniqueConstraint("project_id", "sha256", name="uq_doc_sha_per_project"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id"))
    version_label: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(Enum(DocStatus),
                                        default=DocStatus.PROCESSING)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    entity_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                          nullable=True)
    stats_json: Mapped[dict] = mapped_column(JSON, default=dict)

    document: Mapped[Document] = relationship(
        back_populates="versions", foreign_keys=[document_id])

    __table_args__ = (
        UniqueConstraint("document_id", "version_label",
                         name="uq_version_label_per_doc"),
        Index("ix_dv_document", "document_id"),
    )


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    page_no: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[str] = mapped_column(String(255), default="")
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    has_table: Mapped[bool] = mapped_column(Boolean, default=False)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("version_id", "page_no", name="uq_page_per_version"),
        Index("ix_pages_version", "version_id"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    chunk_seq: Mapped[int] = mapped_column(Integer)
    chunk_id: Mapped[str] = mapped_column(String(64))     # chroma id (M2)
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    section_path: Mapped[str] = mapped_column(String(255), default="")
    section_no: Mapped[str] = mapped_column(String(64), default="")
    chunk_type: Mapped[str] = mapped_column(String(20), default="text")
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String(120), default="")
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                          nullable=True)

    __table_args__ = (
        UniqueConstraint("version_id", "chunk_seq",
                         name="uq_chunk_seq_per_version"),
        Index("ix_chunks_version_page", "version_id", "page_start"),
        Index("ix_chunks_section", "version_id", "section_no"),
    )


class Component(Base):
    __tablename__ = "components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    entity_id: Mapped[str] = mapped_column(String(64))    # e.g. C-01
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(50), default="")
    layer: Mapped[str] = mapped_column(String(50), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(20), default="deterministic")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(Enum(EntityStatus),
                                        default=EntityStatus.PENDING)

    __table_args__ = (
        UniqueConstraint("version_id", "entity_id",
                         name="uq_component_per_version"),
        Index("ix_components_version", "version_id"),
    )


class Interface(Base):
    __tablename__ = "interfaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    entity_id: Mapped[str] = mapped_column(String(64))    # e.g. IF-01
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(20), default="")
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    consumers_json: Mapped[list] = mapped_column(JSON, default=list)
    page: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(20), default="deterministic")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(Enum(EntityStatus),
                                        default=EntityStatus.PENDING)

    __table_args__ = (
        UniqueConstraint("version_id", "entity_id",
                         name="uq_interface_per_version"),
        Index("ix_interfaces_version", "version_id"),
    )


class Port(Base):
    __tablename__ = "ports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    entity_id: Mapped[str] = mapped_column(String(64))    # e.g. P-001
    component_id: Mapped[str] = mapped_column(String(64))
    interface_id: Mapped[str] = mapped_column(String(64), default="")
    direction: Mapped[str] = mapped_column(String(20))    # provides/requires
    page: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(20), default="deterministic")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(Enum(EntityStatus),
                                        default=EntityStatus.PENDING)

    __table_args__ = (
        UniqueConstraint("version_id", "entity_id",
                         name="uq_port_per_version"),
        Index("ix_ports_version", "version_id"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    entity_id: Mapped[str] = mapped_column(String(64))    # e.g. SG-001
    name: Mapped[str] = mapped_column(String(255))
    datatype: Mapped[str] = mapped_column(String(50), default="")
    unit: Mapped[str] = mapped_column(String(30), default="")
    interface_id: Mapped[str] = mapped_column(String(64), default="")
    page: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(20), default="deterministic")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(Enum(EntityStatus),
                                        default=EntityStatus.PENDING)

    __table_args__ = (
        UniqueConstraint("version_id", "entity_id",
                         name="uq_signal_per_version"),
        Index("ix_signals_version", "version_id"),
    )


class Dependency(Base):
    __tablename__ = "dependencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    entity_id: Mapped[str] = mapped_column(String(64))    # e.g. DEP-01
    source_id: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(64))
    relationship: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(20), default="deterministic")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(Enum(EntityStatus),
                                        default=EntityStatus.PENDING)

    __table_args__ = (
        UniqueConstraint("version_id", "entity_id",
                         name="uq_dependency_per_version"),
        Index("ix_deps_version", "version_id"),
    )


class FunctionalFlow(Base):
    __tablename__ = "functional_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    entity_id: Mapped[str] = mapped_column(String(64))    # e.g. FL-1
    name: Mapped[str] = mapped_column(String(255))
    steps_json: Mapped[list] = mapped_column(JSON, default=list)
    trigger: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(20), default="deterministic")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(Enum(EntityStatus),
                                        default=EntityStatus.PENDING)

    __table_args__ = (
        UniqueConstraint("version_id", "entity_id",
                         name="uq_flow_per_version"),
        Index("ix_flows_version", "version_id"),
    )


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id"))
    finding_id: Mapped[str] = mapped_column(String(32))   # F-001...
    type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(Enum(FindingSeverity))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)
    related_entities_json: Mapped[list] = mapped_column(JSON, default=list)
    origin: Mapped[str] = mapped_column(Enum(FindingOrigin))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(Enum(FindingStatus),
                                        default=FindingStatus.OPEN)
    reviewer_comment: Mapped[str] = mapped_column(Text, default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                         nullable=True)

    __table_args__ = (
        UniqueConstraint("analysis_run_id", "finding_id",
                         name="uq_finding_per_run"),
        Index("ix_findings_run", "analysis_run_id"),
        Index("ix_findings_status", "status"),
    )


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"))
    reviewer: Mapped[str] = mapped_column(String(120))
    decision: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (Index("ix_reviews_finding", "finding_id"),)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    kind: Mapped[str] = mapped_column(String(30))  # extraction|analysis|compare
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime,
                                                         nullable=True)
    params_json: Mapped[dict] = mapped_column(JSON, default=dict)
    stats_json: Mapped[dict] = mapped_column(JSON, default=dict)

    findings: Mapped[list[Finding]] = relationship(back_populates="run")

    __table_args__ = (Index("ix_runs_version", "version_id"),)


class CompareRun(Base):
    __tablename__ = "compare_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id"))
    target_version_id: Mapped[int] = mapped_column(
        ForeignKey("document_versions.id"))
    results_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(120), default="")
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_audit_project_ts", "project_id", "ts"),)


Finding.run = relationship("AnalysisRun", back_populates="findings")
