"""Activity log: a human-readable audit trail of admin actions taken
through the dashboard, distinct from the raw journalctl output on /logs.
Every mutating route calls log() right after (attempting) its action, so
the entry reflects what actually happened - including failures, which are
just as useful to have on record as successes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import models

RETENTION_DAYS = 365
_WIB_OFFSET = timedelta(hours=7)


def log(db: Session, action: str, detail: str = "", ok: bool = True) -> None:
    db.add(models.ActivityLog(action=action, detail=detail, ok=ok))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=RETENTION_DAYS)
    db.query(models.ActivityLog).filter(models.ActivityLog.created_at < cutoff).delete()
    db.commit()


def recent(db: Session, limit: int = 20):
    rows = (
        db.query(models.ActivityLog)
        .order_by(models.ActivityLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "when": (r.created_at + _WIB_OFFSET).strftime("%d %b %Y %H:%M:%S") + " WIB",
            "action": r.action,
            "detail": r.detail,
            "ok": r.ok,
        }
        for r in rows
    ]
