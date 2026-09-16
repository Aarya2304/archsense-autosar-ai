"""Append-only audit trail (governance requirement, PDF p.3 / p.25)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.config import APP_USER, AUDIT_LOG_ENABLED
from backend.storage.models import AuditEvent


def log_event(session: Session, action: str, entity: str = "",
              project_id: int | None = None,
              details: dict | None = None,
              actor: str | None = None) -> None:
    """Record one audit event. Never raises into the caller."""
    if not AUDIT_LOG_ENABLED:
        return
    try:
        session.add(AuditEvent(
            project_id=project_id,
            actor=actor or APP_USER,
            action=action,
            entity=entity,
            details_json=details or {},
        ))
        session.commit()
    except Exception:  # noqa: BLE001 - audit must not break the app
        session.rollback()
