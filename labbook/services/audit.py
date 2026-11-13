"""Audit log helpers (spec §4.12). Callers own the transaction; record() never commits."""

from datetime import date, datetime
from enum import Enum

from ..extensions import db
from ..models import AuditLog, User


def _json_safe(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    return value


def snapshot(obj, *fields: str) -> dict:
    return {f: _json_safe(getattr(obj, f)) for f in fields}


def record(actor: User | None, entity: str, entity_id, action: str, before: dict | None = None,
           after: dict | None = None) -> AuditLog:
    if before is not None and after is not None:
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        before = {k: before.get(k) for k in changed} or None
        after = {k: after.get(k) for k in changed} or None
    entry = AuditLog(
        actor_id=actor.id if actor is not None else None,
        entity=entity,
        entity_id=str(entity_id),
        action=action,
        before=_json_safe(before),
        after=_json_safe(after),
    )
    db.session.add(entry)
    return entry
