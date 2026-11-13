"""Equipment admin operations (FR-E4, FR-X1) with audit logging."""

from sqlalchemy import select

from ..extensions import db
from ..models import EquipmentUnit, Term, User
from . import audit

TYPE_FIELDS = ("name", "category", "description", "safety_notes", "photo_url", "requires_approval",
               "purpose_required", "max_duration_min", "student_advance_days", "is_archived")
UNIT_FIELDS = ("type_id", "lab_id", "asset_tag", "serial_no", "notes", "status")
LAB_FIELDS = ("name", "location", "timezone", "term_hours", "vacation_hours")
TERM_FIELDS = ("name", "start_date", "end_date")
HOLIDAY_FIELDS = ("lab_id", "start_date", "end_date", "reason")
ACCESSORY_FIELDS = ("name", "expected_qty", "sort_order", "is_archived")


class CatalogueError(Exception):
    pass


def save(actor: User, obj, fields: tuple[str, ...], values: dict, entity: str) -> None:
    """Creates or updates `obj` from `values` and writes one audit entry."""
    is_new = obj.id is None
    before = None if is_new else audit.snapshot(obj, *fields)
    for key, value in values.items():
        setattr(obj, key, value)
    if is_new:
        db.session.add(obj)
    db.session.flush()
    audit.record(actor, entity, obj.id, "create" if is_new else "update", before, audit.snapshot(obj, *fields))


def delete(actor: User, obj, fields: tuple[str, ...], entity: str) -> None:
    audit.record(actor, entity, obj.id, "delete", audit.snapshot(obj, *fields), None)
    db.session.delete(obj)


def check_term_overlap(start, end, exclude_id: int | None = None) -> None:
    stmt = select(Term).where(Term.start_date <= end, Term.end_date >= start)
    if exclude_id is not None:
        stmt = stmt.where(Term.id != exclude_id)
    clash = db.session.scalar(stmt.limit(1))
    if clash is not None:
        raise CatalogueError(f'Overlaps "{clash.name}" ({clash.start_date} to {clash.end_date}). Terms can\'t overlap.')


def name_taken(model, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(model.id).where(model.name == name)
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    return db.session.scalar(stmt.limit(1)) is not None


def asset_tag_taken(tag: str, exclude_id: int | None = None) -> bool:
    stmt = select(EquipmentUnit.id).where(EquipmentUnit.asset_tag == tag)
    if exclude_id is not None:
        stmt = stmt.where(EquipmentUnit.id != exclude_id)
    return db.session.scalar(stmt.limit(1)) is not None

