"""The Lab Tech desk: check-out, check-in, accessories, no-shows and overdue returns (spec §4.8, §4.15)."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    ACTIVE_STATUSES,
    OVERDUE_GRACE,
    Booking,
    BookingStatus,
    CheckAccessory,
    CheckEvent,
    CheckKind,
    Condition,
    LogKind,
    MaintenanceLog,
    NoShowRecord,
    Role,
    UnitStatus,
    User,
)
from ..rules import Violation
from ..timeutil import default_tz, utcnow
from . import audit, maintenance, notify, settings
from .bookings import BOOKING_AUDIT_FIELDS, BookingRefused
from .db import write_transaction

CHECK_OUT_EARLY = timedelta(minutes=30)
REVERSE_WINDOW = timedelta(hours=24)
NEXT_BOOKING_WARNING = timedelta(hours=2)


@dataclass
class DeskDay:
    overdue: list[Booking] = field(default_factory=list)
    due_out: list[Booking] = field(default_factory=list)
    checked_out: list[Booking] = field(default_factory=list)
    done: list[Booking] = field(default_factory=list)
    missing: list[Booking] = field(default_factory=list)  # open "accessories missing" flags


def desk_day(day: date, now: datetime | None = None) -> DeskDay:
    """Today view (FR-K1): bookings grouped for the lab counter. Overdue items are pinned regardless of date."""
    now = now or utcnow()
    tz = default_tz()
    start = datetime.combine(day, time.min, tz)
    end = start + timedelta(days=1)
    rows = db.session.scalars(
        select(Booking).where(
            or_(Booking.status == BookingStatus.CHECKED_OUT,
                (Booking.starts_at < end) & (Booking.ends_at > start)),
            Booking.status.in_((BookingStatus.CONFIRMED, BookingStatus.CHECKED_OUT, BookingStatus.COMPLETED,
                                BookingStatus.NO_SHOW)),
        ).order_by(Booking.starts_at)
    ).all()
    result = DeskDay()
    for b in rows:
        if b.status is BookingStatus.CHECKED_OUT:
            (result.overdue if now > b.ends_at + OVERDUE_GRACE else result.checked_out).append(b)
        elif b.status is BookingStatus.CONFIRMED:
            result.due_out.append(b)
        else:
            result.done.append(b)
    result.missing = list(db.session.scalars(
        select(Booking).where(Booking.accessories_missing.is_(True)).order_by(Booking.ends_at)
    ))
    return result


def outgoing_items(booking: Booking) -> list[CheckAccessory]:
    event = db.session.scalar(select(CheckEvent).where(CheckEvent.booking_id == booking.id,
                                                       CheckEvent.kind == CheckKind.OUT).order_by(CheckEvent.id.desc()))
    return list(event.items) if event else []


def _record(booking: Booking, kind: CheckKind, actor: User, condition: Condition, notes: str,
            items: dict[int, tuple[int, bool]], now: datetime) -> CheckEvent:
    event = CheckEvent(booking_id=booking.id, kind=kind, at=now, staff_id=actor.id, condition=condition,
                       notes=notes.strip())
    for accessory_id, (qty, missing) in items.items():
        event.items.append(CheckAccessory(accessory_id=accessory_id, qty=qty, is_missing=missing))
    db.session.add(event)
    db.session.flush()
    return event


def check_out(booking: Booking, actor: User, condition: Condition, notes: str, handed: dict[int, int],
              now: datetime | None = None) -> CheckEvent:
    """FR-K1 / FR-X2: hand the unit over, recording condition and which accessories went out."""
    now = now or utcnow()
    if booking.status is not BookingStatus.CONFIRMED:
        raise BookingRefused([Violation("status", "Only confirmed bookings can be checked out.")])
    if now < booking.starts_at - CHECK_OUT_EARLY:
        opens = (booking.starts_at - CHECK_OUT_EARLY).astimezone(default_tz())
        raise BookingRefused([Violation("early", f"Too early: check-out opens at {opens:%H:%M}.")])
    if now >= booking.ends_at:
        raise BookingRefused([Violation("late", "This booking has already ended.")])
    before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
    expected = {a.id: a.expected_qty for a in booking.type.active_accessories}
    items = {aid: (max(0, min(handed.get(aid, 0), 99)), False) for aid in expected}
    event = _record(booking, CheckKind.OUT, actor, condition, notes, items, now)
    booking.status = BookingStatus.CHECKED_OUT
    audit.record(actor, "Booking", booking.id, "check_out", before, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
    return event


def check_in(booking: Booking, actor: User, condition: Condition, notes: str, returned: dict[int, int],
             take_out_of_service: bool = False, now: datetime | None = None) -> tuple[CheckEvent, list[str]]:
    """FR-K1 / FR-X3: receive the unit back. Items not returned are flagged missing, logged and notified.
    A damaged return can take the unit out of service, which relocates its later bookings."""
    now = now or utcnow()
    with write_transaction():
        if booking.status is not BookingStatus.CHECKED_OUT:
            raise BookingRefused([Violation("status", "This booking isn't checked out.")])
        before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
        items, missing_text = {}, []
        for out in outgoing_items(booking):
            back = max(0, min(returned.get(out.accessory_id, 0), out.qty))
            missing = back < out.qty
            items[out.accessory_id] = (back, missing)
            if missing:
                missing_text.append(f"{out.accessory.name} ×{out.qty - back}")
        event = _record(booking, CheckKind.IN, actor, condition, notes, items, now)
        booking.status = BookingStatus.COMPLETED
        unit = booking.unit

        if missing_text:
            booking.accessories_missing = True
            listing = ", ".join(missing_text)
            maintenance.add_log(unit, LogKind.ISSUE, f"Accessories missing at check-in: {listing}", actor, booking)
            body = f"Missing after {booking.type.name} ({unit.asset_tag}) on {notify.when(booking)}: {listing}."
            notify.notify(booking.user, notify.ACCESSORIES_MISSING, "Accessories missing", body + " Please return "
                          "them to the lab desk.", notify.booking_path(booking))
            for tech in notify.lab_techs():
                if tech.id != actor.id:
                    notify.notify(tech, notify.ACCESSORIES_MISSING, f"Accessories missing: {unit.asset_tag}",
                                  body, notify.url_path("desk.index"))
        if condition is Condition.DAMAGED:
            maintenance.add_log(unit, LogKind.ISSUE, f"Returned damaged. {notes}".strip(), actor, booking)
        audit.record(actor, "Booking", booking.id, "check_in", before, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
        if take_out_of_service and unit.status is UnitStatus.AVAILABLE:
            reason = f"Damaged at check-in of booking #{booking.id}. {notes}".strip()
            maintenance.apply_status(unit, UnitStatus.OUT_OF_SERVICE, reason, actor)
        return event, missing_text


def resolve_missing(booking: Booking, actor: User, now: datetime | None = None) -> None:
    """FR-X4: the missing items were returned or replaced."""
    now = now or utcnow()
    open_items = db.session.scalars(
        select(CheckAccessory).join(CheckEvent).where(CheckEvent.booking_id == booking.id,
                                                      CheckAccessory.is_missing.is_(True),
                                                      CheckAccessory.resolved_at.is_(None))
    ).all()
    for item in open_items:
        item.resolved_at = now
        item.resolved_by_id = actor.id
    booking.accessories_missing = False
    for entry in db.session.scalars(select(MaintenanceLog).where(
            MaintenanceLog.booking_id == booking.id, MaintenanceLog.kind == LogKind.ISSUE,
            MaintenanceLog.resolved_at.is_(None), MaintenanceLog.notes.like("Accessories missing%"))):
        entry.resolved_at = now
    audit.record(actor, "Booking", booking.id, "resolve_missing", {"accessories_missing": True},
                 {"accessories_missing": False})


# --- No-shows (FR-K2, FR-K3) ----------------------------------------------------------------------

def _active_no_shows(user: User, now: datetime) -> int:
    window = timedelta(days=settings.get("no_show_window_days"))
    return db.session.scalar(
        select(func.count()).select_from(NoShowRecord)
        .where(NoShowRecord.user_id == user.id, NoShowRecord.reversed_at.is_(None),
               NoShowRecord.created_at > now - window)
    )


def mark_no_show(booking: Booking, actor: User | None, now: datetime | None = None) -> Booking:
    """Confirmed but not collected. Frees the rest of the slot; students get a penalty record and may be
    suspended after `no_show_limit` no-shows within `no_show_window_days`."""
    now = now or utcnow()
    if booking.status is not BookingStatus.CONFIRMED:
        raise BookingRefused([Violation("status", "Only confirmed bookings can be marked as no-shows.")])
    if now < booking.starts_at:
        raise BookingRefused([Violation("early", "The booking hasn't started yet.")])
    before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
    booking.status = BookingStatus.NO_SHOW
    audit.record(actor, "Booking", booking.id, "no_show", before, audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
    user = booking.user
    notify.notify(user, notify.NO_SHOW, "Marked as a no-show",
                  f"{booking.type.name} on {notify.when(booking)} wasn't collected, so it was released.",
                  notify.booking_path(booking))
    if user is None or booking.user_role_at_booking is not Role.STUDENT:
        return booking
    record = NoShowRecord(user_id=user.id, booking_id=booking.id, created_at=now,
                          marked_by_id=actor.id if actor else None)
    db.session.add(record)
    db.session.flush()
    if _active_no_shows(user, now) >= settings.get("no_show_limit") and not user.is_suspended:
        until = now + timedelta(days=settings.get("suspension_days"))
        before_user = {"suspended_until": None}
        user.suspended_until = until
        record.caused_suspension = True
        audit.record(actor, "User", user.id, "suspend", before_user, {"suspended_until": until.isoformat()})
        local = until.astimezone(default_tz())
        notify.notify(user, notify.SUSPENSION_STARTED, "Booking suspended",
                      f"After {settings.get('no_show_limit')} no-shows in {settings.get('no_show_window_days')} "
                      f"days you can't make new bookings until {local:%a %d %b, %H:%M}. Your existing "
                      f"bookings still stand.", notify.url_path("main.home"))
    return booking


def reverse_no_show(booking: Booking, actor: User, now: datetime | None = None) -> Booking:
    """FR-K2: undo a no-show marked by mistake, within 24 hours."""
    now = now or utcnow()
    with write_transaction():
        if booking.status is not BookingStatus.NO_SHOW:
            raise BookingRefused([Violation("status", "This booking isn't a no-show.")])
        record = db.session.scalar(select(NoShowRecord).where(NoShowRecord.booking_id == booking.id))
        marked_at = record.created_at if record else booking.updated_at
        if now - marked_at > REVERSE_WINDOW:
            raise BookingRefused([Violation("window", "No-shows can only be reversed within 24 hours.")])
        before = audit.snapshot(booking, *BOOKING_AUDIT_FIELDS)
        target = BookingStatus.CONFIRMED if booking.ends_at > now else BookingStatus.COMPLETED
        try:
            with db.session.begin_nested():
                booking.status = target
        except IntegrityError:
            raise BookingRefused([Violation("taken", "The unit has been booked by someone else since.")]) from None
        audit.record(actor, "Booking", booking.id, "reverse_no_show", before,
                     audit.snapshot(booking, *BOOKING_AUDIT_FIELDS))
        if record is not None:
            record.reversed_at = now
            user = booking.user
            if (record.caused_suspension and user is not None and user.is_suspended
                    and _active_no_shows(user, now) < settings.get("no_show_limit")):
                lift_suspension(user, actor, "A no-show was reversed.")
        return booking


def lift_suspension(user: User, actor: User | None, why: str) -> None:
    before = {"suspended_until": user.suspended_until.isoformat() if user.suspended_until else None}
    user.suspended_until = None
    audit.record(actor, "User", user.id, "lift_suspension", before, {"suspended_until": None})
    notify.notify(user, notify.SUSPENSION_LIFTED, "You can book again",
                  f"Your booking suspension has been lifted. {why}", notify.url_path("catalogue.index"))


def detect_no_shows(now: datetime) -> int:
    grace = timedelta(minutes=settings.get("grace_minutes"))
    due = db.session.scalars(
        select(Booking).where(Booking.status == BookingStatus.CONFIRMED, Booking.starts_at < now - grace)
    ).all()
    for booking in due:
        mark_no_show(booking, None, now)
    return len(due)


def detect_overdue(now: datetime) -> int:
    """FR-K4: still out 15 minutes after the end. Tell the booker and lab techs once, and warn the next
    booker of the same unit if their booking is coming up."""
    due = db.session.scalars(
        select(Booking).where(Booking.status == BookingStatus.CHECKED_OUT, Booking.overdue_notified_at.is_(None),
                              Booking.ends_at < now - OVERDUE_GRACE)
    ).all()
    for booking in due:
        booking.overdue_notified_at = now
        unit = booking.unit
        notify.notify(booking.user, notify.OVERDUE, "Equipment overdue",
                      f"{booking.type.name} ({unit.asset_tag}) was due back at the end of your booking on "
                      f"{notify.when(booking)}. Please return it to {unit.lab.name} now.",
                      notify.booking_path(booking))
        for tech in notify.lab_techs():
            notify.notify(tech, notify.OVERDUE, f"Overdue: {unit.asset_tag}",
                          f"{booking.user.name if booking.user else 'A user'} hasn't returned {unit.asset_tag} "
                          f"({booking.type.name}), due at the end of {notify.when(booking)}.",
                          notify.url_path("desk.index"))
        following = db.session.scalar(
            select(Booking).where(Booking.unit_id == unit.id, Booking.id != booking.id,
                                  Booking.status.in_(ACTIVE_STATUSES), Booking.starts_at >= booking.ends_at,
                                  Booking.starts_at <= now + NEXT_BOOKING_WARNING)
            .order_by(Booking.starts_at).limit(1)
        )
        if following is not None:
            notify.notify(following.user, notify.NEXT_BOOKING_AT_RISK, "Your unit may not be back in time",
                          f"{unit.asset_tag} hasn't been returned by the previous user yet. The lab desk will "
                          f"move you to another unit if it isn't back for {notify.when(following)}.",
                          notify.booking_path(following))
    return len(due)
