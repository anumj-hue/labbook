"""Creating, rescheduling and cancelling bookings (spec §4.3, §4.5, §7.3).

Every write runs inside write_transaction() (SQLite BEGIN IMMEDIATE), re-checks the rules inside the lock,
then inserts. The overlap triggers back this up: an insert that would overlap raises IntegrityError, and
assignment moves on to the next candidate unit. Callers commit.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import rules
from ..extensions import db
from ..models import (
    ACTIVE_STATUSES,
    HOURS_STATUSES,
    QUOTA_STATUSES,
    AuditLog,
    Booking,
    BookingStatus,
    EquipmentType,
    EquipmentUnit,
    Role,
    UnitStatus,
    User,
)
from ..rules import Violation
from ..timeutil import default_tz, utcnow
from . import audit, settings
from .availability import free_units, in_service_units, type_timezone
from .calendar import LabDayCache
from .db import write_transaction

BOOKING_AUDIT_FIELDS = ("unit_id", "type_id", "user_id", "starts_at", "ends_at", "status", "purpose",
                        "cancel_reason", "is_late_cancel")
UNDO_WINDOW = timedelta(seconds=30)


class BookingRefused(Exception):
    def __init__(self, violations: list[Violation]):
        self.violations = violations
        super().__init__("; ".join(v.message for v in violations))


@dataclass
class BookingRequest:
    user: User
    eq_type: EquipmentType
    starts_at: datetime
    ends_at: datetime
    unit_id: int | None = None
    purpose: str = ""
    booking: Booking | None = None  # set when rescheduling an existing booking


@dataclass
class Evaluation:
    violations: list[Violation]
    free: list[EquipmentUnit] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _existing_for(user: User, since: datetime, exclude_id: int | None) -> tuple[rules.ExistingBooking, ...]:
    statuses = set(ACTIVE_STATUSES) | set(HOURS_STATUSES)
    stmt = select(Booking).where(Booking.user_id == user.id, Booking.status.in_(statuses), Booking.ends_at > since)
    if exclude_id is not None:
        stmt = stmt.where(Booking.id != exclude_id)
    return tuple(
        rules.ExistingBooking(
            type_id=b.type_id, starts_at=b.starts_at, ends_at=b.ends_at,
            is_active=b.status in ACTIVE_STATUSES,
            counts_for_quota=b.status in QUOTA_STATUSES,
            counts_for_hours=b.status in HOURS_STATUSES,
        )
        for b in db.session.scalars(stmt)
    )


def evaluate(req: BookingRequest, now: datetime | None = None) -> Evaluation:
    """Checks a request against every rule and finds the free units, without writing anything."""
    now = now or utcnow()
    eq_type = req.eq_type
    user = req.user
    is_student = user.role is Role.STUDENT
    extra: list[Violation] = []

    if eq_type.is_archived:
        extra.append(Violation("archived", "This equipment is no longer bookable."))
    if is_student and eq_type.requires_approval:
        extra.append(Violation("approval", "This equipment needs teacher approval. Requests for it open soon; "
                                           "ask a lab tech in the meantime."))

    pinned: EquipmentUnit | None = None
    if req.unit_id:
        pinned = db.session.get(EquipmentUnit, req.unit_id)
        if pinned is None or pinned.type_id != eq_type.id or pinned.status is not UnitStatus.AVAILABLE:
            extra.append(Violation("unit", "That unit isn't available for booking."))
            pinned = None

    tz = type_timezone(eq_type)
    day = req.starts_at.astimezone(tz).date()
    cache = LabDayCache()
    candidate_units = [pinned] if pinned else in_service_units(eq_type)
    labs = {u.lab.id: u.lab for u in candidate_units}
    lab_days = tuple(cache.get(lab, day) for lab in labs.values())

    exclude_id = req.booking.id if req.booking else None
    free = free_units(eq_type, req.starts_at, req.ends_at, unit_id=pinned.id if pinned else None,
                      exclude_booking_id=exclude_id, prefer_unit_id=req.booking.unit_id if req.booking else None,
                      lab_days=cache) if req.ends_at > req.starts_at else []

    effective = settings.effective_rules(eq_type)
    advance = effective["student_advance_days"] if is_student else effective["staff_advance_days"]
    week_start = datetime.combine(day - timedelta(days=day.weekday()), datetime.min.time(), tz)
    ctx = rules.BookingContext(
        now=now,
        tz=tz,
        starts_at=req.starts_at,
        ends_at=req.ends_at,
        type_id=eq_type.id,
        is_student=is_student,
        is_verified=user.is_verified,
        suspended_until=user.suspended_until,
        max_duration_minutes=effective["max_duration_min"],
        advance_days=advance,
        student_max_active=settings.get("student_max_active"),
        student_max_weekly_hours=settings.get("student_max_weekly_hours"),
        lab_days=lab_days,
        existing=_existing_for(user, min(now, week_start), exclude_id),
        purpose_required=eq_type.purpose_required,
        purpose=req.purpose,
        free_unit_count=len(free) if candidate_units else 0,
        unit_label=pinned.asset_tag if pinned else None,
    )
    return Evaluation(extra + rules.check(ctx), free)


def _try_assign(booking: Booking, units: list[EquipmentUnit], values: dict, is_new: bool) -> bool:
    """Puts the booking on the first unit the database accepts; the triggers reject overlaps.

    `values` are re-applied on every attempt because a rolled-back savepoint expires the object.
    """
    for unit in units:
        try:
            with db.session.begin_nested():
                for key, value in values.items():
                    setattr(booking, key, value)
                booking.unit_id = unit.id
                if is_new:
                    db.session.add(booking)
        except IntegrityError:
            continue
        return True
    return False


def create(req: BookingRequest, actor: User) -> Booking:
    with write_transaction():
        result = evaluate(req)
        if not result.ok:
            raise BookingRefused(result.violations)
        booking = Booking(
            type_id=req.eq_type.id,
            user_id=req.user.id,
            created_by_id=actor.id,
            starts_at=req.starts_at,
            ends_at=req.ends_at,
            status=BookingStatus.CONFIRMED,  # approval requests (Pending) arrive in slice 3
            user_role_at_booking=req.user.role,
            purpose=req.purpose.strip(),
        )
        if not _try_assign(booking, result.free, {}, is_new=True):
            raise BookingRefused([Violation("no_units", "No units available for this time.")])
        audit.record(actor, "Booking", booking.id, "create", None, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
        return booking


def reschedule(booking: Booking, starts_at: datetime, ends_at: datetime, unit_id: int | None, purpose: str,
               actor: User) -> Booking:
    """FR-C3: change the time (and optionally the unit) of a booking that hasn't started."""
    with write_transaction():
        if not booking.can_change:
            raise BookingRefused([Violation("locked", "This booking can no longer be changed.")])
        before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
        req = BookingRequest(booking.user, booking.type, starts_at, ends_at, unit_id, purpose, booking=booking)
        result = evaluate(req)
        if not result.ok:
            raise BookingRefused(result.violations)
        values = {"starts_at": starts_at, "ends_at": ends_at, "purpose": purpose.strip()}
        if not _try_assign(booking, result.free, values, is_new=False):
            raise BookingRefused([Violation("no_units", "No units available for this time.")])
        audit.record(actor, "Booking", booking.id, "reschedule", before,
                     audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
        return booking


def cancel(booking: Booking, actor: User, reason: str | None = None, now: datetime | None = None) -> Booking:
    """FR-C1/C2: cancel before the start; within late_cancel_minutes it's recorded as a late cancellation."""
    now = now or utcnow()
    if booking.status not in QUOTA_STATUSES and booking.status is not BookingStatus.OFFERED:
        raise BookingRefused([Violation("locked", "Only pending or confirmed bookings can be cancelled.")])
    if booking.starts_at <= now:
        raise BookingRefused([Violation("started", "This booking has already started.")])
    before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_by_id = actor.id
    booking.cancelled_at = now
    booking.cancel_reason = reason
    booking.is_late_cancel = booking.starts_at - now < timedelta(minutes=settings.get("late_cancel_minutes"))
    audit.record(actor, "Booking", booking.id, "cancel", before, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
    return booking


def can_undo_cancel(booking: Booking, actor: User, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (booking.status is BookingStatus.CANCELLED and booking.cancelled_by_id == actor.id
            and booking.cancelled_at is not None and now - booking.cancelled_at <= UNDO_WINDOW
            and booking.starts_at > now)


def undo_cancel(booking: Booking, actor: User) -> Booking:
    """Spec §6.10: Undo a cancellation shortly after, as long as nobody has taken the slot meanwhile."""
    with write_transaction():
        if not can_undo_cancel(booking, actor):
            raise BookingRefused([Violation("undo", "It's too late to undo that cancellation.")])
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.entity == "Booking", AuditLog.entity_id == str(booking.id),
                                   AuditLog.action == "cancel").order_by(AuditLog.id.desc()).limit(1)
        )
        previous = BookingStatus((entry.before or {}).get("status", "CONFIRMED")) if entry else BookingStatus.CONFIRMED
        before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
        try:
            with db.session.begin_nested():
                booking.status = previous
                booking.cancelled_by_id = booking.cancelled_at = booking.cancel_reason = None
                booking.is_late_cancel = False
        except IntegrityError:
            raise BookingRefused([Violation("taken", "Someone else has booked that time.")]) from None
        audit.record(actor, "Booking", booking.id, "undo_cancel", before,
                     audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
        return booking


def cancel_future_for_user(user: User, actor: User, reason: str) -> int:
    """FR-A7: deactivating a user cancels their future bookings."""
    now = utcnow()
    bookings = db.session.scalars(
        select(Booking).where(Booking.user_id == user.id, Booking.status.in_(ACTIVE_STATUSES),
                              Booking.status != BookingStatus.CHECKED_OUT, Booking.starts_at > now)
    ).all()
    for booking in bookings:
        cancel(booking, actor, reason, now=now)
    return len(bookings)


def quota_summary(user: User, now: datetime | None = None) -> dict[str, int]:
    """For the student quota meter: active bookings and hours booked this ISO week."""
    now = now or utcnow()
    tz = default_tz()
    today = now.astimezone(tz).date()
    week_start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time(), tz)
    week_end = week_start + timedelta(days=7)
    rows = db.session.scalars(
        select(Booking).where(Booking.user_id == user.id, Booking.status.in_(HOURS_STATUSES),
                              Booking.ends_at > min(now, week_start))
    ).all()
    active = sum(1 for b in rows if b.status in QUOTA_STATUSES and b.ends_at > now)
    minutes = sum(b.duration_minutes for b in rows if week_start <= b.starts_at < week_end)
    return {
        "active": active,
        "max_active": settings.get("student_max_active"),
        "week_minutes": minutes,
        "max_week_hours": settings.get("student_max_weekly_hours"),
    }

