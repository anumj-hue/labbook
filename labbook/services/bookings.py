"""Creating, approving, rescheduling, overriding and cancelling bookings (spec §4.3–§4.7, §7.3).

Every write that can create an overlap runs inside write_transaction() (SQLite BEGIN IMMEDIATE),
re-checks the rules inside the lock, then inserts. The overlap triggers back this up: a write that would
overlap raises IntegrityError, and assignment moves on to the next candidate unit. Callers commit;
notifications are sent only after that commit (services/notify.py).
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from sqlalchemy import or_, select
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
from ..rules import SLOT_MINUTES, Violation
from ..timeutil import default_tz, utcnow
from . import audit, notify, settings
from .availability import busy_by_unit, free_units, in_service_units, type_timezone
from .calendar import LabDayCache
from .db import write_transaction

BOOKING_AUDIT_FIELDS = ("unit_id", "type_id", "user_id", "starts_at", "ends_at", "status", "purpose",
                        "cancel_reason", "is_late_cancel", "decision_reason", "bumped_by_booking_id")
UNDO_WINDOW = timedelta(seconds=30)
AVAILABILITY_CODES = {"no_units", "unit_busy"}


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
class OverrideOption:
    """A student booking a Teacher/Admin may bump to get a unit (spec §4.7)."""

    unit: EquipmentUnit
    booking: Booking


@dataclass
class Evaluation:
    violations: list[Violation]
    free: list[EquipmentUnit] = field(default_factory=list)
    override: OverrideOption | None = None

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def only_availability(self) -> bool:
        """True when the only problem is that no unit is free (so an override could help)."""
        return bool(self.violations) and all(v.code in AVAILABILITY_CODES for v in self.violations)


def needs_approval(user: User, eq_type: EquipmentType) -> bool:
    """FR-B4: student bookings of approval-required equipment start as Pending; staff are confirmed."""
    return user.role is Role.STUDENT and eq_type.requires_approval


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


def _override_option(eq_type: EquipmentType, start: datetime, end: datetime, pinned: EquipmentUnit | None,
                     cache: LabDayCache, now: datetime) -> OverrideOption | None:
    """FR-O1–O5: a unit whose only conflict is one bumpable student booking; the most recently made wins."""
    units = [pinned] if pinned else in_service_units(eq_type)
    day = start.astimezone(type_timezone(eq_type)).date()
    units = [u for u in units if cache.get(u.lab, day).contains(start, end)]
    busy = busy_by_unit([u.id for u in units], start, end)
    min_notice = timedelta(hours=settings.get("override_min_notice_hours"))
    options = []
    for unit in units:
        items = busy.get(unit.id, [])
        if len(items) != 1 or items[0].booking is None:
            continue
        victim = items[0].booking
        if (victim.user_role_at_booking is Role.STUDENT
                and victim.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED)
                and victim.starts_at - now >= min_notice):
            options.append(OverrideOption(unit, victim))
    return max(options, key=lambda o: (o.booking.created_at, o.booking.id), default=None)


def evaluate(req: BookingRequest, now: datetime | None = None) -> Evaluation:
    """Checks a request against every rule and finds the free units, without writing anything."""
    now = now or utcnow()
    eq_type = req.eq_type
    user = req.user
    is_student = user.role is Role.STUDENT
    extra: list[Violation] = []

    if eq_type.is_archived:
        extra.append(Violation("archived", "This equipment is no longer bookable."))

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
    week_start = datetime.combine(day - timedelta(days=day.weekday()), time.min, tz)
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
    result = Evaluation(extra + rules.check(ctx), free)
    if result.only_availability and req.booking is None and user.has_role(Role.TEACHER):
        result.override = _override_option(eq_type, req.starts_at, req.ends_at, pinned, cache, now)
    return result


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


def _notify_new_request(booking: Booking) -> None:
    for approver in notify.approvers():
        notify.notify(approver, notify.NEW_REQUEST, "New booking request",
                      f"{booking.user.name} asked for {booking.type.name} on {notify.when(booking)}.",
                      notify.url_path("approvals.index"), email=False)  # in-app only (spec §4.10)


def create(req: BookingRequest, actor: User, override_reason: str | None = None) -> Booking:
    """Books a unit. With `override_reason`, a Teacher/Admin may bump a student booking (spec §4.7)."""
    now = utcnow()
    with write_transaction():
        result = evaluate(req, now)
        bumped: Booking | None = None
        units = result.free
        if not result.ok:
            if override_reason is None or not result.only_availability or result.override is None:
                raise BookingRefused(result.violations)
            if not override_reason.strip():
                raise BookingRefused([Violation("override_reason",
                                                "Give a reason for overriding the student booking.")])
            bumped = result.override.booking
            units = [result.override.unit]
            before = audit.snapshot(bumped, *BOOKING_AUDIT_FIELDS)
            bumped.status = BookingStatus.BUMPED
            bumped.cancelled_by_id = actor.id
            bumped.cancelled_at = now
            bumped.cancel_reason = override_reason.strip()
            db.session.flush()

        booking = Booking(
            type_id=req.eq_type.id,
            user_id=req.user.id,
            created_by_id=actor.id,
            starts_at=req.starts_at,
            ends_at=req.ends_at,
            status=BookingStatus.PENDING if needs_approval(req.user, req.eq_type) else BookingStatus.CONFIRMED,
            user_role_at_booking=req.user.role,
            purpose=req.purpose.strip(),
        )
        if not _try_assign(booking, units, {}, is_new=True):
            raise BookingRefused([Violation("no_units", "No units available for this time.")])
        audit.record(actor, "Booking", booking.id, "create", None, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))

        if bumped is not None:
            bumped.bumped_by_booking_id = booking.id
            audit.record(actor, "Booking", bumped.id, "override", before,
                         audit.snapshot(bumped, *BOOKING_AUDIT_FIELDS))
            _notify_bumped(bumped, override_reason.strip())

        if booking.status is BookingStatus.PENDING:
            notify.notify(req.user, notify.REQUEST_SUBMITTED, "Booking request sent",
                          f"Your request for {booking.type.name} on {notify.when(booking)} is waiting for a "
                          f"teacher's approval. Unit {booking.unit.asset_tag} is held for you meanwhile.",
                          notify.booking_path(booking))
            _notify_new_request(booking)
        else:
            notify.notify(req.user, notify.BOOKING_CONFIRMED, "Booking confirmed",
                          f"{booking.type.name} ({booking.unit.asset_tag}) is booked for you on "
                          f"{notify.when(booking)} in {booking.unit.lab.name}.",
                          notify.booking_path(booking))
        return booking


def _notify_bumped(bumped: Booking, reason: str) -> None:
    """FR-O3: tell the student why, with a rebook link and nearby free times where possible."""
    tz = type_timezone(bumped.type)
    suggestions = suggest_slots(bumped.type, bumped.starts_at, bumped.ends_at)
    lines = [f"Your booking of {bumped.type.name} on {notify.when(bumped)} was overridden by a teacher.",
             f"Reason: {reason}"]
    if suggestions:
        times = "; ".join(f"{s.astimezone(tz):%a %d %b %H:%M}–{e.astimezone(tz):%H:%M}" for s, e in suggestions)
        lines.append(f"Free times you could rebook: {times}.")
    notify.notify(bumped.user, notify.BUMPED, "Your booking was overridden", "\n".join(lines),
                  notify.url_path("catalogue.detail", type_id=bumped.type_id,
                                  date=bumped.starts_at.astimezone(tz).date().isoformat()))


def suggest_slots(eq_type: EquipmentType, start: datetime, end: datetime, limit: int = 3,
                  days: int = 3) -> list[tuple[datetime, datetime]]:
    """Free ranges of the same length on the same and following days, nearest first."""
    now = utcnow()
    tz = type_timezone(eq_type)
    length = end - start
    step = timedelta(minutes=SLOT_MINUTES)
    cache = LabDayCache()
    found: list[tuple[datetime, datetime]] = []
    first_day = start.astimezone(tz).date()
    labs = {u.lab.id: u.lab for u in in_service_units(eq_type)}
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        for lab in labs.values():
            for open_, close in cache.get(lab, day).intervals:
                t = open_
                while t + length <= close:
                    if t > now and (t, t + length) not in found and free_units(eq_type, t, t + length, lab_days=cache):
                        found.append((t, t + length))
                    t += step
    found.sort(key=lambda r: abs(r[0] - start))
    return sorted(found[:limit])


def reschedule(booking: Booking, starts_at: datetime, ends_at: datetime, unit_id: int | None, purpose: str,
               actor: User) -> Booking:
    """FR-C3: change the time of a booking that hasn't started. For approval-required equipment a
    student's confirmed booking goes back to Pending."""
    with write_transaction():
        if not booking.can_change:
            raise BookingRefused([Violation("locked", "This booking can no longer be changed.")])
        before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
        req = BookingRequest(booking.user, booking.type, starts_at, ends_at, unit_id, purpose, booking=booking)
        result = evaluate(req)
        if not result.ok:
            raise BookingRefused(result.violations)
        values = {"starts_at": starts_at, "ends_at": ends_at, "purpose": purpose.strip()}
        back_to_pending = booking.status is BookingStatus.CONFIRMED and needs_approval(booking.user, booking.type)
        if back_to_pending:
            values.update(status=BookingStatus.PENDING, decided_by_id=None, decided_at=None, decision_reason=None)
        if not _try_assign(booking, result.free, values, is_new=False):
            raise BookingRefused([Violation("no_units", "No units available for this time.")])
        audit.record(actor, "Booking", booking.id, "reschedule", before,
                     audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
        if booking.status is BookingStatus.PENDING:
            _notify_new_request(booking)
        return booking


# --- Approvals (spec §4.4) ------------------------------------------------------------------------

def _decide(booking: Booking, actor: User, now: datetime) -> dict:
    if booking.status is not BookingStatus.PENDING:
        raise BookingRefused([Violation("decided", "This request has already been dealt with.")])
    if booking.starts_at <= now:
        raise BookingRefused([Violation("expired", "This request's start time has passed.")])
    before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
    booking.decided_by_id = actor.id
    booking.decided_at = now
    return before


def approve(booking: Booking, actor: User, now: datetime | None = None) -> Booking:
    now = now or utcnow()
    before = _decide(booking, actor, now)
    booking.status = BookingStatus.CONFIRMED
    audit.record(actor, "Booking", booking.id, "approve", before, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
    notify.notify(booking.user, notify.REQUEST_APPROVED, "Booking approved",
                  f"{actor.name} approved your request for {booking.type.name} on {notify.when(booking)}. "
                  f"Collect unit {booking.unit.asset_tag} from {booking.unit.lab.name}.",
                  notify.booking_path(booking))
    return booking


def reject(booking: Booking, actor: User, reason: str, now: datetime | None = None) -> Booking:
    now = now or utcnow()
    if not reason or not reason.strip():
        raise BookingRefused([Violation("reason", "Give a reason for rejecting the request.")])
    before = _decide(booking, actor, now)
    booking.status = BookingStatus.REJECTED
    booking.decision_reason = reason.strip()
    audit.record(actor, "Booking", booking.id, "reject", before, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
    notify.notify(booking.user, notify.REQUEST_REJECTED, "Booking request rejected",
                  f"Your request for {booking.type.name} on {notify.when(booking)} was rejected.\n"
                  f"Reason: {booking.decision_reason}",
                  notify.booking_path(booking))
    return booking


def expire_due(now: datetime | None = None) -> int:
    """FR-P3: Pending requests expire at their start time or after pending_expiry_hours, whichever is first."""
    now = now or utcnow()
    cutoff = now - timedelta(hours=settings.get("pending_expiry_hours"))
    due = db.session.scalars(
        select(Booking).where(Booking.status == BookingStatus.PENDING,
                              or_(Booking.starts_at <= now, Booking.created_at <= cutoff))
    ).all()
    for booking in due:
        before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
        booking.status = BookingStatus.EXPIRED
        booking.decided_at = now
        audit.record(None, "Booking", booking.id, "expire", before, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
        notify.notify(booking.user, notify.REQUEST_EXPIRED, "Booking request expired",
                      f"Your request for {booking.type.name} on {notify.when(booking)} wasn't approved in time, "
                      f"so the unit has been released. You can request another time.",
                      notify.booking_path(booking))
    return len(due)


# --- Cancelling -----------------------------------------------------------------------------------

def cancel(booking: Booking, actor: User, reason: str | None = None, now: datetime | None = None) -> Booking:
    """FR-C1/C2/C4: cancel before the start. Within late_cancel_minutes it's a late cancellation.
    Staff cancelling someone else's booking must give a reason, and the owner is told."""
    now = now or utcnow()
    by_someone_else = actor.id != booking.user_id
    if booking.status not in QUOTA_STATUSES and booking.status is not BookingStatus.OFFERED:
        raise BookingRefused([Violation("locked", "Only pending or confirmed bookings can be cancelled.")])
    if booking.starts_at <= now:
        raise BookingRefused([Violation("started", "This booking has already started.")])
    if by_someone_else and not (reason and reason.strip()):
        raise BookingRefused([Violation("reason", "Give a reason for cancelling someone else's booking.")])
    before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_by_id = actor.id
    booking.cancelled_at = now
    booking.cancel_reason = reason.strip() if reason else None
    booking.is_late_cancel = (not by_someone_else
                              and booking.starts_at - now < timedelta(minutes=settings.get("late_cancel_minutes")))
    audit.record(actor, "Booking", booking.id, "cancel", before, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
    if by_someone_else:
        notify.notify(booking.user, notify.CANCELLED_BY_STAFF, "Booking cancelled by staff",
                      f"Your booking of {booking.type.name} on {notify.when(booking)} was cancelled by "
                      f"{actor.name}.\nReason: {booking.cancel_reason}",
                      notify.booking_path(booking))
    return booking


def can_undo_cancel(booking: Booking, actor: User, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (booking.status is BookingStatus.CANCELLED and booking.cancelled_by_id == actor.id
            and booking.user_id == actor.id and booking.cancelled_at is not None
            and now - booking.cancelled_at <= UNDO_WINDOW and booking.starts_at > now)


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
    week_start = datetime.combine(today - timedelta(days=today.weekday()), time.min, tz)
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
