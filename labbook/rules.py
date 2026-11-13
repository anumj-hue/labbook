"""Booking rules engine (spec §4.3 FR-B3, §4.6).

Pure Python: no Flask, no database. The service layer (services/bookings.py) gathers the facts into a
BookingContext and calls check(); the same function serves live validation in the booking picker and
enforcement on submit, so the two can never disagree. The server is authoritative.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SLOT_MINUTES = 15
MIN_DURATION_MINUTES = 15


@dataclass(frozen=True)
class Violation:
    code: str
    message: str


@dataclass(frozen=True)
class LabDay:
    """Opening hours of one lab on one local date. `intervals` are timezone-aware [start, end) pairs."""

    lab_id: int
    lab_name: str
    intervals: tuple[tuple[datetime, datetime], ...]
    holiday: str | None = None  # reason, when closed for a holiday or closure

    @property
    def is_open(self) -> bool:
        return bool(self.intervals)

    def contains(self, start: datetime, end: datetime) -> bool:
        return any(open_ <= start and end <= close for open_, close in self.intervals)

    def hours_text(self) -> str:
        return ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in self.intervals)


@dataclass(frozen=True)
class ExistingBooking:
    """One of the booker's other bookings. Flags are decided by the caller from the booking status."""

    type_id: int
    starts_at: datetime
    ends_at: datetime
    is_active: bool  # blocks time (Offered, Pending, Confirmed, Checked out)
    counts_for_quota: bool  # Pending or Confirmed (spec §4.6)
    counts_for_hours: bool  # booked hours in the ISO week


@dataclass(frozen=True)
class BookingContext:
    now: datetime
    tz: ZoneInfo
    starts_at: datetime
    ends_at: datetime
    type_id: int
    is_student: bool
    is_verified: bool
    suspended_until: datetime | None
    max_duration_minutes: int
    advance_days: int
    student_max_active: int
    student_max_weekly_hours: int
    lab_days: tuple[LabDay, ...]
    existing: tuple[ExistingBooking, ...] = ()
    purpose_required: bool = False
    purpose: str = ""
    free_unit_count: int | None = None  # None: availability not checked
    unit_label: str | None = None  # set when the booker picked a specific unit


def format_minutes(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    return f"{hours} h" if hours else f"{mins} min"


def _on_slot(value: datetime, tz: ZoneInfo) -> bool:
    local = value.astimezone(tz)
    return local.minute % SLOT_MINUTES == 0 and local.second == 0 and local.microsecond == 0


def check(ctx: BookingContext) -> list[Violation]:
    """Returns every rule the request breaks; an empty list means it can be booked."""
    v: list[Violation] = []
    start_local = ctx.starts_at.astimezone(ctx.tz)
    end_local = ctx.ends_at.astimezone(ctx.tz)
    duration = int((ctx.ends_at - ctx.starts_at).total_seconds() // 60)

    if not ctx.is_verified:
        v.append(Violation("unverified", "Verify your email address before booking."))
    if ctx.suspended_until is not None and ctx.suspended_until > ctx.now:
        until = ctx.suspended_until.astimezone(ctx.tz)
        v.append(Violation("suspended", f"You can't make new bookings until {until:%a} {until.day} {until:%b}, "
                                        f"{until:%H:%M} because of missed bookings."))

    # 1. Start is in the future.
    if ctx.starts_at <= ctx.now:
        v.append(Violation("past", "Pick a start time in the future."))

    # Shape: 15-minute steps, at least 15 minutes, a single day.
    if not (_on_slot(ctx.starts_at, ctx.tz) and _on_slot(ctx.ends_at, ctx.tz)):
        v.append(Violation("slot", "Times must be on 15-minute steps."))
    if duration < MIN_DURATION_MINUTES:
        v.append(Violation("too_short", f"Bookings must be at least {MIN_DURATION_MINUTES} minutes."))
    elif start_local.date() != (end_local - timedelta(microseconds=1)).date():
        v.append(Violation("multi_day", "A booking must start and end on the same day."))

    # 2. Advance booking window (dates in the lab's timezone).
    days_ahead = (start_local.date() - ctx.now.astimezone(ctx.tz).date()).days
    if days_ahead > ctx.advance_days:
        who = "Students" if ctx.is_student else "Staff"
        v.append(Violation("window", f"{who} can book up to {ctx.advance_days} days ahead."))

    # 3. Maximum duration.
    if duration > ctx.max_duration_minutes:
        v.append(Violation("too_long", f"Max {format_minutes(ctx.max_duration_minutes)} for this equipment."))

    # Opening hours and closures.
    if duration >= MIN_DURATION_MINUTES and not any(day.contains(ctx.starts_at, ctx.ends_at) for day in ctx.lab_days):
        holidays = [day.holiday for day in ctx.lab_days if day.holiday]
        open_days = [day for day in ctx.lab_days if day.is_open]
        if holidays and not open_days:
            v.append(Violation("closed", f"Closed — {holidays[0]}."))
        elif not open_days:
            v.append(Violation("closed", "The lab is closed that day."))
        else:
            v.append(Violation("hours", f"Outside opening hours ({open_days[0].hours_text()})."))

    if ctx.purpose_required and not ctx.purpose.strip():
        v.append(Violation("purpose", "Add a purpose for this booking."))

    # 6. Student quotas (Pending and Confirmed both count; spec §4.6).
    if ctx.is_student:
        upcoming = sum(1 for b in ctx.existing if b.counts_for_quota and b.ends_at > ctx.now)
        if upcoming >= ctx.student_max_active:
            v.append(Violation("quota_active", f"You already have {upcoming} upcoming bookings, the most a student "
                                               f"can hold ({ctx.student_max_active})."))
        week = start_local.isocalendar()[:2]
        week_minutes = sum(
            int((b.ends_at - b.starts_at).total_seconds() // 60)
            for b in ctx.existing
            if b.counts_for_hours and b.starts_at.astimezone(ctx.tz).isocalendar()[:2] == week
        )
        limit = ctx.student_max_weekly_hours * 60
        if week_minutes + duration > limit:
            v.append(Violation("quota_hours", f"This would take you to {format_minutes(week_minutes + duration)} "
                                              f"booked that week (max {ctx.student_max_weekly_hours} h)."))

    # 8. No overlapping booking of the same equipment type.
    if any(b.is_active and b.type_id == ctx.type_id and b.starts_at < ctx.ends_at and ctx.starts_at < b.ends_at
           for b in ctx.existing):
        v.append(Violation("own_overlap", "You already have this equipment booked at an overlapping time."))

    # 7. A unit is free (only meaningful once the time itself is valid).
    if ctx.free_unit_count == 0 and not any(x.code in {"closed", "hours"} for x in v):
        if ctx.unit_label:
            v.append(Violation("unit_busy", f"{ctx.unit_label} isn't free then. Pick another time or any unit."))
        else:
            v.append(Violation("no_units", "No units available for this time."))

    return v
