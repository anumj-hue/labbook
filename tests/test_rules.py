"""Unit tests for the pure rules engine (no app, no database)."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from labbook.rules import BookingContext, ExistingBooking, LabDay, check, format_minutes

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=TZ)  # a Friday


def at(day_offset: int, hh: int, mm: int = 0) -> datetime:
    base = NOW.date() + timedelta(days=day_offset)
    return datetime(base.year, base.month, base.day, hh, mm, tzinfo=TZ)


def lab_day(day_offset: int, open_=(8, 0), close=(18, 0), holiday=None) -> LabDay:
    if holiday:
        return LabDay(1, "Lab", (), holiday=holiday)
    return LabDay(1, "Lab", ((at(day_offset, *open_), at(day_offset, *close)),))


def ctx(start=None, end=None, day=2, **overrides) -> BookingContext:
    start = start or at(day, 10)
    end = end or at(day, 12)
    base = BookingContext(
        now=NOW, tz=TZ, starts_at=start, ends_at=end, type_id=1, is_student=True, is_verified=True,
        suspended_until=None, max_duration_minutes=180, advance_days=14, student_max_active=3,
        student_max_weekly_hours=8, lab_days=(lab_day(day),), free_unit_count=2,
    )
    return replace(base, **overrides)


def codes(context) -> set[str]:
    return {v.code for v in check(context)}


def booking(day, h1, h2, type_id=2, active=True, quota=True, hours=True):
    return ExistingBooking(type_id, at(day, h1), at(day, h2), active, quota, hours)


def test_valid_request_passes():
    assert check(ctx()) == []


def test_start_must_be_in_future():
    assert "past" in codes(ctx(start=at(0, 8), end=at(0, 10), day=0, lab_days=(lab_day(0),)))


def test_fifteen_minute_steps_and_minimum_length():
    assert "slot" in codes(ctx(start=at(2, 10, 5), end=at(2, 11)))
    assert "too_short" in codes(ctx(start=at(2, 10), end=at(2, 10)))


def test_single_day_only():
    end = at(3, 9)
    assert "multi_day" in codes(ctx(start=at(2, 17), end=end, max_duration_minutes=24 * 60))


@pytest.mark.parametrize(("days_ahead", "blocked"), [(14, False), (15, True)])
def test_advance_window_acceptance_4(days_ahead, blocked):
    context = ctx(day=days_ahead, lab_days=(lab_day(days_ahead),))
    assert ("window" in codes(context)) is blocked


def test_max_duration_acceptance_5():
    violations = check(ctx(start=at(2, 10), end=at(2, 14)))
    assert [v.message for v in violations if v.code == "too_long"] == ["Max 3 h for this equipment."]


def test_outside_opening_hours_and_closures():
    assert "hours" in codes(ctx(start=at(2, 17), end=at(2, 19)))
    violations = check(ctx(lab_days=(lab_day(2, holiday="Diwali"),)))
    assert any(v.code == "closed" and "Diwali" in v.message for v in violations)
    assert "closed" in codes(ctx(lab_days=(LabDay(1, "Lab", ()),)))


def test_any_open_lab_is_enough():
    closed = LabDay(2, "Other", ())
    assert check(ctx(lab_days=(closed, lab_day(2)))) == []


def test_active_quota_acceptance_3():
    existing = (booking(3, 9, 10), booking(4, 9, 10), booking(5, 9, 10))
    assert "quota_active" in codes(ctx(existing=existing))
    # Staff have no quota.
    assert "quota_active" not in codes(ctx(existing=existing, is_student=False))
    # Past bookings don't count towards active bookings.
    past = (booking(-1, 9, 10), booking(-2, 9, 10), booking(-3, 9, 10))
    assert "quota_active" not in codes(ctx(existing=past))


def test_weekly_hours_quota_uses_iso_week():
    # Booking on Sunday the 15th (same ISO week as Friday the 13th) with 7 h already that week.
    existing = (booking(0, 10, 13, quota=False), booking(1, 10, 14, quota=False))
    sunday = ctx(day=2, existing=existing)
    assert "quota_hours" in codes(sunday)
    # Monday the 16th starts a new ISO week.
    monday = ctx(day=3, lab_days=(lab_day(3),), existing=existing)
    assert "quota_hours" not in codes(monday)


def test_no_overlapping_booking_of_same_type():
    same = (booking(2, 11, 13, type_id=1),)
    assert "own_overlap" in codes(ctx(existing=same))
    other_type = (booking(2, 11, 13, type_id=2),)
    assert "own_overlap" not in codes(ctx(existing=other_type))
    adjacent = (booking(2, 12, 13, type_id=1),)
    assert "own_overlap" not in codes(ctx(existing=adjacent))


def test_suspended_and_unverified_users_blocked():
    assert "suspended" in codes(ctx(suspended_until=NOW + timedelta(days=3)))
    assert "suspended" not in codes(ctx(suspended_until=NOW - timedelta(days=1)))
    assert "unverified" in codes(ctx(is_verified=False))


def test_no_units_free():
    assert "no_units" in codes(ctx(free_unit_count=0))
    assert "unit_busy" in codes(ctx(free_unit_count=0, unit_label="OSC-01"))
    # Outside hours explains itself; don't also claim there are no units.
    assert codes(ctx(start=at(2, 17), end=at(2, 19), free_unit_count=0)) == {"hours"}


def test_purpose_required():
    assert "purpose" in codes(ctx(purpose_required=True, purpose="  "))
    assert "purpose" not in codes(ctx(purpose_required=True, purpose="Lab 4 practical"))


def test_format_minutes():
    assert format_minutes(180) == "3 h"
    assert format_minutes(90) == "1 h 30 min"
    assert format_minutes(45) == "45 min"
