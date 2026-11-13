"""Out-of-service units, maintenance blocks, logs and issue reports (spec §4.9).

When a unit goes out of service, is retired, or gets a maintenance block, the bookings it can no longer
honour are moved to another free unit of the same type where possible, otherwise cancelled; either way
the booker is told (FR-M1, FR-M2). Bookings already checked out are left alone.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    ACTIVE_STATUSES,
    Booking,
    BookingStatus,
    EquipmentUnit,
    LogKind,
    MaintenanceBlock,
    MaintenanceLog,
    UnitStatus,
    User,
)
from ..rules import Violation
from ..timeutil import utcnow
from . import audit, notify
from .availability import free_units
from .bookings import BOOKING_AUDIT_FIELDS, BookingRefused, cancel, try_assign
from .catalogue import UNIT_FIELDS
from .db import write_transaction

BLOCK_FIELDS = ("unit_id", "starts_at", "ends_at", "reason")


@dataclass
class Relocation:
    moved: list[Booking] = field(default_factory=list)
    cancelled: list[Booking] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.moved:
            parts.append(f"{len(self.moved)} booking{'s' if len(self.moved) != 1 else ''} moved to another unit")
        if self.cancelled:
            parts.append(f"{len(self.cancelled)} cancelled")
        return " and ".join(parts)


def add_log(unit: EquipmentUnit, kind: LogKind, notes: str, actor: User | None,
            booking: Booking | None = None) -> MaintenanceLog:
    entry = MaintenanceLog(unit_id=unit.id, kind=kind, notes=notes.strip(), reported_by_id=actor.id if actor else None,
                           booking_id=booking.id if booking else None)
    db.session.add(entry)
    db.session.flush()
    return entry


def relocate_or_cancel(bookings: list[Booking], actor: User, reason: str) -> Relocation:
    """Moves each booking to another free unit of its type, or cancels it (FR-M1). Caller holds the lock."""
    result = Relocation()
    for booking in bookings:
        before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
        old_tag = booking.unit.asset_tag
        candidates = [u for u in free_units(booking.type, booking.starts_at, booking.ends_at,
                                            exclude_booking_id=booking.id) if u.id != booking.unit_id]
        if candidates and try_assign(booking, candidates, {}, is_new=False):
            audit.record(actor, "Booking", booking.id, "reassign", before,
                         audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
            notify.notify(booking.user, notify.UNIT_REASSIGNED, "Your booking moved to another unit",
                          f"{old_tag} isn't available, so your booking of {booking.type.name} on "
                          f"{notify.when(booking)} now uses {booking.unit.asset_tag}.",
                          notify.booking_path(booking))
            result.moved.append(booking)
        else:
            cancel(booking, actor, reason)
            result.cancelled.append(booking)
    return result


def _affected(unit_ids: list[int], start: datetime | None = None, end: datetime | None = None) -> list[Booking]:
    """Bookings that haven't been handed over and still block the unit(s) in the period."""
    now = utcnow()
    stmt = select(Booking).where(
        Booking.unit_id.in_(unit_ids), Booking.status.in_(ACTIVE_STATUSES),
        Booking.status != BookingStatus.CHECKED_OUT, Booking.starts_at > now,
    ).order_by(Booking.starts_at)
    if start is not None:
        stmt = stmt.where(Booking.starts_at < end, Booking.ends_at > start)
    return list(db.session.scalars(stmt))


def apply_status(unit: EquipmentUnit, status: UnitStatus, reason: str, actor: User) -> Relocation:
    """Changes a unit's status inside an existing write transaction."""
    if status is unit.status:
        return Relocation()
    if status is not UnitStatus.AVAILABLE and not reason.strip():
        raise BookingRefused([Violation("reason", "Give a reason for taking the unit out of service.")])
    before = audit.snapshot(unit, *UNIT_FIELDS)
    previous = unit.status
    unit.status = status
    db.session.flush()
    audit.record(actor, "EquipmentUnit", unit.id, "status", before, audit.snapshot(unit, *UNIT_FIELDS))
    if status is UnitStatus.AVAILABLE:
        add_log(unit, LogKind.BACK_IN_SERVICE, reason or f"Back in service (was {previous.label.lower()}).", actor)
        return Relocation()
    note = reason if status is UnitStatus.OUT_OF_SERVICE else f"Retired. {reason}"
    add_log(unit, LogKind.OUT_OF_SERVICE, note, actor)
    return relocate_or_cancel(_affected([unit.id]), actor, f"{unit.asset_tag} is out of service: {reason}")


def set_unit_status(unit: EquipmentUnit, status: UnitStatus, reason: str, actor: User) -> Relocation:
    with write_transaction():
        return apply_status(unit, status, reason, actor)


def create_blocks(units: list[EquipmentUnit], start: datetime, end: datetime, reason: str,
                  actor: User) -> tuple[list[MaintenanceBlock], Relocation]:
    """FR-M2: block a unit (or every unit of a type) for a time range; clashing bookings move or are cancelled."""
    if end <= start:
        raise BookingRefused([Violation("time", "The block must end after it starts.")])
    if not reason.strip():
        raise BookingRefused([Violation("reason", "Give a reason for the block.")])
    with write_transaction():
        blocks = []
        for unit in units:
            block = MaintenanceBlock(unit_id=unit.id, starts_at=start, ends_at=end, reason=reason.strip(),
                                     created_by_id=actor.id)
            try:
                with db.session.begin_nested():
                    db.session.add(block)
            except IntegrityError:
                raise BookingRefused([Violation("overlap", f"{unit.asset_tag} already has a block then.")]) from None
            audit.record(actor, "MaintenanceBlock", block.id, "create", None, audit.snapshot(block, *BLOCK_FIELDS))
            blocks.append(block)
        relocation = relocate_or_cancel(_affected([u.id for u in units], start, end), actor,
                                        f"Maintenance: {reason.strip()}")
        return blocks, relocation


def delete_block(block: MaintenanceBlock, actor: User) -> None:
    audit.record(actor, "MaintenanceBlock", block.id, "delete", audit.snapshot(block, *BLOCK_FIELDS), None)
    db.session.delete(block)


def report_issue(booking: Booking, user: User, notes: str) -> MaintenanceLog:
    """FR-M4: a booker reports a problem with the unit they used."""
    if not notes.strip():
        raise BookingRefused([Violation("notes", "Describe the issue.")])
    entry = add_log(booking.unit, LogKind.ISSUE, notes, user, booking)
    audit.record(user, "MaintenanceLog", entry.id, "report_issue", None, {"unit_id": booking.unit_id})
    for tech in notify.lab_techs():
        notify.notify(tech, notify.ISSUE_REPORTED, f"Issue reported on {booking.unit.asset_tag}",
                      f"{user.name}: {notes.strip()}", notify.url_path("units.detail", unit_id=booking.unit_id))
    return entry


def resolve_log(entry: MaintenanceLog, actor: User) -> None:
    if entry.resolved_at is None:
        entry.resolved_at = utcnow()
        audit.record(actor, "MaintenanceLog", entry.id, "resolve", None, {"resolved": True})
