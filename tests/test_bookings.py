import threading
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from labbook.extensions import db as _db
from labbook.models import (
    AuditLog,
    Booking,
    BookingStatus,
    EquipmentType,
    EquipmentUnit,
    Holiday,
    Lab,
    MaintenanceBlock,
    Role,
    Term,
    UnitStatus,
)
from labbook.services import bookings as svc
from labbook.services import calendar
from labbook.services import settings as settings_svc
from labbook.timeutil import utcnow

TZ = ZoneInfo("Asia/Kolkata")
EVERY_DAY = {d: [["08:00", "18:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


def local_day(offset: int) -> date:
    return utcnow().astimezone(TZ).date() + timedelta(days=offset)


def at(offset: int, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(local_day(offset), time(hh, mm), TZ)


@pytest.fixture
def scope(db):
    lab = Lab(name="Electronics", timezone="Asia/Kolkata", term_hours=EVERY_DAY, vacation_hours=EVERY_DAY)
    eq_type = EquipmentType(name="Oscilloscope", category="Scopes")
    db.session.add_all([lab, eq_type])
    db.session.flush()
    units = [EquipmentUnit(type_id=eq_type.id, lab_id=lab.id, asset_tag=f"OSC-0{i}") for i in (1, 2)]
    db.session.add_all(units)
    db.session.commit()
    return {"lab": lab, "type": eq_type, "units": units}


def book_form(eq_type, offset=2, start="10:00", end="12:00", unit=0, purpose=""):
    return {"type_id": eq_type.id, "date": local_day(offset).isoformat(), "start": start, "end": end,
            "unit_id": unit, "purpose": purpose}


def add_booking(user, eq_type, unit, start, end, status=BookingStatus.CONFIRMED):
    booking = Booking(type_id=eq_type.id, unit_id=unit.id, user_id=user.id, created_by_id=user.id, starts_at=start,
                      ends_at=end, status=status, user_role_at_booking=user.role)
    _db.session.add(booking)
    _db.session.commit()
    return booking


# --- Create ---------------------------------------------------------------------------------------

def test_student_books_and_gets_confirmed(login_as, client, db, scope):
    user = login_as()
    response = client.post("/bookings/new", data=book_form(scope["type"]))
    assert response.status_code == 302, response.text
    booking = db.session.query(Booking).one()
    assert booking.status is BookingStatus.CONFIRMED
    assert booking.user_id == user.id and booking.user_role_at_booking is Role.STUDENT
    assert booking.starts_at == at(2, 10)
    assert db.session.query(AuditLog).filter_by(entity="Booking", action="create").count() == 1
    assert client.get(response.location).status_code == 200


def test_assigns_unit_with_fewest_bookings_that_day(login_as, client, make_user, db, scope):
    other = make_user()
    osc1, osc2 = scope["units"]
    add_booking(other, scope["type"], osc1, at(2, 8), at(2, 9))
    login_as()
    client.post("/bookings/new", data=book_form(scope["type"]))
    mine = db.session.query(Booking).filter(Booking.user_id != other.id).one()
    assert mine.unit_id == osc2.id


def test_pinned_unit(login_as, client, db, scope):
    login_as()
    osc1 = scope["units"][0]
    client.post("/bookings/new", data=book_form(scope["type"], unit=osc1.id))
    assert db.session.query(Booking).one().unit_id == osc1.id


def test_rule_violations_are_shown(login_as, client, db, scope):
    login_as()
    response = client.post("/bookings/new", data=book_form(scope["type"], start="10:00", end="14:00"))
    assert response.status_code == 400
    assert "Max 3 h for this equipment." in response.text
    assert db.session.query(Booking).count() == 0


def test_quota_blocks_fourth_booking_acceptance_3(login_as, client, db, scope):
    login_as()
    for offset in (1, 2, 3):
        assert client.post("/bookings/new", data=book_form(scope["type"], offset=offset, start="09:00",
                                                           end="10:00")).status_code == 302
    response = client.post("/bookings/new", data=book_form(scope["type"], offset=4, start="09:00", end="10:00"))
    assert "3 upcoming bookings" in response.text
    assert db.session.query(Booking).count() == 3


def test_window_blocks_15_days_ahead_acceptance_4(login_as, client, db, scope):
    login_as()
    response = client.post("/bookings/new", data=book_form(scope["type"], offset=15))
    assert "up to 14 days ahead" in response.text
    assert client.post("/bookings/new", data=book_form(scope["type"], offset=14)).status_code == 302


def test_unverified_user_cannot_book(login_as, client, db, scope):
    login_as(verified=False)
    response = client.post("/bookings/new", data=book_form(scope["type"]))
    assert "Verify your email" in response.text


def test_holiday_blocks_booking(login_as, client, db, scope):
    db.session.add(Holiday(start_date=local_day(2), end_date=local_day(2), reason="Diwali"))
    db.session.commit()
    login_as()
    response = client.post("/bookings/new", data=book_form(scope["type"]))
    assert "Closed — Diwali" in response.text


def test_students_cannot_book_approval_equipment_yet_but_staff_can(login_as, client, db, scope):
    scope["type"].requires_approval = True
    db.session.commit()
    login_as()
    assert "needs teacher approval" in client.post("/bookings/new", data=book_form(scope["type"])).text
    client.post("/logout")
    login_as(Role.TEACHER)
    assert client.post("/bookings/new", data=book_form(scope["type"])).status_code == 302
    assert db.session.query(Booking).one().status is BookingStatus.CONFIRMED


def test_no_units_available_when_all_taken(login_as, client, make_user, db, scope):
    other = make_user()
    for unit in scope["units"]:
        add_booking(other, scope["type"], unit, at(2, 9), at(2, 13))
    login_as()
    response = client.post("/bookings/new", data=book_form(scope["type"]))
    assert "No units available" in response.text


def test_maintenance_block_makes_unit_unavailable(login_as, client, db, scope):
    osc1 = scope["units"][0]
    db.session.add(MaintenanceBlock(unit_id=osc1.id, starts_at=at(2, 8), ends_at=at(2, 18), reason="Calibration"))
    db.session.commit()
    login_as()
    response = client.post("/bookings/new", data=book_form(scope["type"], unit=osc1.id))
    assert "OSC-01 isn&#39;t free" in response.text
    assert client.post("/bookings/new", data=book_form(scope["type"])).status_code == 302
    assert db.session.query(Booking).one().unit_id == scope["units"][1].id


# --- Database guarantees --------------------------------------------------------------------------

def test_trigger_rejects_overlapping_insert(make_user, db, scope):
    user = make_user()
    osc1 = scope["units"][0]
    add_booking(user, scope["type"], osc1, at(2, 10), at(2, 12))
    with pytest.raises(IntegrityError, match="booking_overlap"):
        add_booking(user, scope["type"], osc1, at(2, 11), at(2, 13))
    db.session.rollback()
    # Touching ranges are fine ([start, end)), and inactive bookings don't block.
    add_booking(user, scope["type"], osc1, at(2, 12), at(2, 13))
    add_booking(user, scope["type"], osc1, at(2, 10), at(2, 11), status=BookingStatus.CANCELLED)


def test_trigger_rejects_booking_over_maintenance(make_user, db, scope):
    osc1 = scope["units"][0]
    db.session.add(MaintenanceBlock(unit_id=osc1.id, starts_at=at(2, 9), ends_at=at(2, 11), reason="Repair"))
    db.session.commit()
    with pytest.raises(IntegrityError, match="maintenance_overlap"):
        add_booking(make_user(), scope["type"], osc1, at(2, 10), at(2, 12))
    db.session.rollback()


def test_trigger_rejects_reactivating_into_taken_slot(make_user, db, scope):
    user = make_user()
    osc1 = scope["units"][0]
    cancelled = add_booking(user, scope["type"], osc1, at(2, 10), at(2, 12), status=BookingStatus.CANCELLED)
    add_booking(user, scope["type"], osc1, at(2, 10), at(2, 12))
    cancelled.status = BookingStatus.CONFIRMED
    with pytest.raises(IntegrityError, match="booking_overlap"):
        db.session.commit()
    db.session.rollback()


def test_concurrent_requests_for_last_unit_acceptance_1(app, db, make_user, scope):
    """Two students book the last free unit at the same moment: exactly one succeeds."""
    scope["units"][1].status = UnitStatus.RETIRED
    db.session.commit()
    students = [make_user(), make_user()]
    type_id = scope["type"].id
    barrier = threading.Barrier(2)
    results: list[str] = []

    def attempt(user_id: int):
        with app.app_context():
            from labbook.models import User

            user = _db.session.get(User, user_id)
            eq_type = _db.session.get(EquipmentType, type_id)
            req = svc.BookingRequest(user, eq_type, at(2, 10), at(2, 12))
            barrier.wait()
            try:
                svc.create(req, user)
                _db.session.commit()
                results.append("ok")
            except svc.BookingRefused as refused:
                _db.session.rollback()
                results.append(refused.violations[0].message)
            except Exception as exc:  # surface thread errors in the assertion
                results.append(f"error: {exc!r}")
            finally:
                _db.session.remove()

    db.session.commit()
    threads = [threading.Thread(target=attempt, args=(s.id,)) for s in students]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["No units available for this time.", "ok"]
    db.session.rollback()  # end this session's read snapshot, taken before the threads wrote
    assert db.session.query(Booking).count() == 1


# --- Change & cancel ------------------------------------------------------------------------------

def test_reschedule_keeps_unit_when_free(login_as, client, db, scope):
    login_as()
    client.post("/bookings/new", data=book_form(scope["type"]))
    booking = db.session.query(Booking).one()
    unit_id = booking.unit_id
    data = book_form(scope["type"], start="13:00", end="15:00") | {"booking_id": booking.id}
    assert client.post(f"/bookings/{booking.id}/edit", data=data).status_code == 302
    db.session.refresh(booking)
    assert booking.starts_at == at(2, 13) and booking.unit_id == unit_id
    assert db.session.query(AuditLog).filter_by(entity="Booking", action="reschedule").count() == 1


def test_reschedule_ignores_own_slot_when_overlapping(login_as, client, db, scope):
    scope["units"][1].status = UnitStatus.RETIRED
    db.session.commit()
    login_as()
    client.post("/bookings/new", data=book_form(scope["type"]))
    booking = db.session.query(Booking).one()
    data = book_form(scope["type"], start="11:00", end="13:00") | {"booking_id": booking.id}
    assert client.post(f"/bookings/{booking.id}/edit", data=data).status_code == 302
    db.session.refresh(booking)
    assert booking.starts_at == at(2, 11)


def test_cancel_and_late_cancel_flag(login_as, client, make_user, db, scope):
    user = login_as()
    early = add_booking(user, scope["type"], scope["units"][0], at(2, 10), at(2, 11))
    client.post(f"/bookings/{early.id}/cancel", data={})
    db.session.refresh(early)
    assert early.status is BookingStatus.CANCELLED and not early.is_late_cancel

    soon = utcnow() + timedelta(minutes=30)
    late = add_booking(user, scope["type"], scope["units"][1], soon, soon + timedelta(hours=1))
    response = client.post(f"/bookings/{late.id}/cancel", data={}, follow_redirects=True)
    db.session.refresh(late)
    assert late.is_late_cancel and "late cancellation" in response.text


def test_undo_cancel(login_as, client, db, scope):
    login_as()
    client.post("/bookings/new", data=book_form(scope["type"]))
    booking = db.session.query(Booking).one()
    response = client.post(f"/bookings/{booking.id}/cancel", data={}, follow_redirects=True)
    assert "Undo" in response.text
    client.post(f"/bookings/{booking.id}/undo-cancel")
    db.session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED and booking.cancelled_at is None


def test_cannot_touch_someone_elses_booking(login_as, client, make_user, db, scope):
    other = make_user()
    booking = add_booking(other, scope["type"], scope["units"][0], at(2, 10), at(2, 11))
    login_as()
    assert client.get(f"/bookings/{booking.id}").status_code == 404
    assert client.post(f"/bookings/{booking.id}/cancel", data={}).status_code == 404
    assert client.get(f"/bookings/{booking.id}/edit").status_code == 404


def test_deactivating_user_cancels_future_bookings(login_as, client, make_user, db, scope):
    student = make_user()
    booking = add_booking(student, scope["type"], scope["units"][0], at(2, 10), at(2, 11))
    login_as(Role.ADMIN)
    response = client.post(f"/admin/users/{student.id}/deactivate", follow_redirects=True)
    assert "1 future booking cancelled" in response.text
    db.session.refresh(booking)
    assert booking.status is BookingStatus.CANCELLED and booking.cancel_reason == "Account deactivated"


# --- Validate endpoint, pages, privacy ------------------------------------------------------------

def test_validate_endpoint(login_as, client, scope):
    login_as()
    ok = client.post("/bookings/validate", data=book_form(scope["type"])).get_json()
    assert ok["ok"] and ok["violations"] == [] and ok["summary"]["duration"] == "2 h"
    bad = client.post("/bookings/validate", data=book_form(scope["type"], start="10:00", end="14:00")).get_json()
    assert not bad["ok"] and "too_long" in {v["code"] for v in bad["violations"]}
    incomplete = client.post("/bookings/validate", data=book_form(scope["type"], start="")).get_json()
    assert incomplete["incomplete"]


def test_my_bookings_tabs(login_as, client, db, scope):
    user = login_as()
    add_booking(user, scope["type"], scope["units"][0], at(2, 10), at(2, 11))
    add_booking(user, scope["type"], scope["units"][0], at(-2, 10), at(-2, 11), status=BookingStatus.COMPLETED)
    upcoming = client.get("/bookings").text
    past = client.get("/bookings?tab=past").text
    assert upcoming.count("Confirmed</span>") >= 1 and "Completed" not in upcoming
    assert "Completed" in past


def test_calendar_hides_names_from_students(login_as, client, make_user, db, scope):
    other = make_user(name="Priya Secret")
    add_booking(other, scope["type"], scope["units"][0], at(1, 10), at(1, 11))
    login_as()
    page = client.get(f"/calendar?date={local_day(1).isoformat()}").text
    assert "Priya Secret" not in page and "Booked" in page
    client.post("/logout")
    login_as(Role.LAB_TECH)
    assert "Priya Secret" in client.get(f"/calendar?date={local_day(1).isoformat()}").text


def test_timeline_on_detail_page(login_as, client, make_user, db, scope):
    other = make_user(name="Hidden Name")
    add_booking(other, scope["type"], scope["units"][0], at(2, 10), at(2, 11))
    login_as()
    page = client.get(f"/equipment/{scope['type'].id}?date={local_day(2).isoformat()}").text
    assert "data-timeline" in page and "OSC-01" in page
    assert "Hidden Name" not in page


def test_catalogue_free_now_and_free_between(login_as, client, make_user, db, scope):
    other = make_user()
    for unit in scope["units"]:
        add_booking(other, scope["type"], unit, at(2, 9), at(2, 13))
    login_as()
    page = client.get(f"/equipment/?free_date={local_day(2).isoformat()}&free_from=10:00&free_to=11:00").text
    assert "Oscilloscope" not in page
    page = client.get(f"/equipment/?free_date={local_day(2).isoformat()}&free_from=14:00&free_to=15:00").text
    assert "Oscilloscope" in page


def test_home_shows_next_booking_and_quota(login_as, client, db, scope):
    user = login_as()
    add_booking(user, scope["type"], scope["units"][0], at(2, 10), at(2, 11))
    page = client.get("/").text
    assert "Next booking" in page and "Oscilloscope" in page
    assert "1 of 3" in page


# --- Opening hours --------------------------------------------------------------------------------

def test_term_vs_vacation_hours_and_lab_specific_holiday(db, scope):
    lab = scope["lab"]
    lab.term_hours = {"mon": [["08:00", "18:00"]]}
    lab.vacation_hours = {"mon": [["09:00", "13:00"]]}
    monday = local_day(0) + timedelta(days=(7 - local_day(0).weekday()) % 7 or 7)
    db.session.add(Term(name="T", start_date=monday, end_date=monday))
    db.session.commit()
    assert calendar.lab_day(lab, monday).hours_text() == "08:00–18:00"
    assert calendar.lab_day(lab, monday + timedelta(days=7)).hours_text() == "09:00–13:00"
    assert not calendar.lab_day(lab, monday + timedelta(days=1)).is_open

    other = Lab(name="Other", term_hours=EVERY_DAY)
    db.session.add(other)
    db.session.flush()
    db.session.add(Holiday(lab_id=other.id, start_date=monday, end_date=monday, reason="Pest control"))
    db.session.commit()
    assert calendar.lab_day(other, monday).holiday == "Pest control"
    assert calendar.lab_day(lab, monday).is_open


def test_settings_change_applies_to_rules(login_as, client, db, scope):
    settings_svc.set_value("max_duration_min", 60, None)
    db.session.commit()
    login_as()
    response = client.post("/bookings/new", data=book_form(scope["type"]))
    assert "Max 1 h for this equipment." in response.text


def test_journal_mode_is_wal(db):
    assert db.session.execute(text("PRAGMA journal_mode")).scalar() == "wal"
