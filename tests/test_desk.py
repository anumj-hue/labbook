"""Slice 4: lab desk, no-shows, overdue, maintenance, closures, CSV export, audit viewer."""

import csv
import io
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import login

from labbook.extensions import db as _db
from labbook.jobs import tick
from labbook.models import (
    Accessory,
    AuditLog,
    Booking,
    BookingStatus,
    CheckEvent,
    EquipmentType,
    EquipmentUnit,
    Lab,
    LogKind,
    MaintenanceBlock,
    MaintenanceLog,
    NoShowRecord,
    Notification,
    Role,
    UnitStatus,
)
from labbook.services import desk
from labbook.timeutil import utcnow

TZ = ZoneInfo("Asia/Kolkata")
ALL_DAY = {d: [["00:00", "23:45"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


def local_day(offset: int):
    return utcnow().astimezone(TZ).date() + timedelta(days=offset)


def at(offset: int, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(local_day(offset), time(hh, mm), TZ)


@pytest.fixture
def lab(db):
    lab = Lab(name="Electronics", timezone="Asia/Kolkata", term_hours=ALL_DAY, vacation_hours=ALL_DAY)
    db.session.add(lab)
    db.session.commit()
    return lab


@pytest.fixture
def scope(db, lab):
    eq_type = EquipmentType(name="Scope", category="C")
    db.session.add(eq_type)
    db.session.flush()
    db.session.add_all([EquipmentUnit(type_id=eq_type.id, lab_id=lab.id, asset_tag=f"OSC-0{i}") for i in (1, 2)])
    db.session.add(Accessory(type_id=eq_type.id, name="Probe", expected_qty=2))
    db.session.commit()
    return eq_type


def add_booking(user, eq_type, start, end, status=BookingStatus.CONFIRMED, unit=None):
    unit = unit or eq_type.units[0]
    booking = Booking(type_id=eq_type.id, unit_id=unit.id, user_id=user.id, created_by_id=user.id, starts_at=start,
                      ends_at=end, status=status, user_role_at_booking=user.role)
    _db.session.add(booking)
    _db.session.commit()
    return booking


def as_user(client, user):
    client.post("/logout")
    assert login(client, user.email).status_code == 302


def now_slot(minutes_from_now: int, length_min: int = 60):
    start = (utcnow() + timedelta(minutes=minutes_from_now)).replace(second=0, microsecond=0)
    return start, start + timedelta(minutes=length_min)


# --- Check-out / check-in, accessories (acceptance #9, #14) ---------------------------------------

def test_check_out_and_in_with_missing_accessory_acceptance_14(client, make_user, db, scope, mailbox):
    student = make_user(email="s@example.edu")
    tech = make_user(Role.LAB_TECH, email="tech@example.edu")
    other_tech = make_user(Role.LAB_TECH, email="tech2@example.edu")
    start, end = now_slot(-10)
    booking = add_booking(student, scope, start, end)
    probe = scope.accessories[0]
    as_user(client, tech)

    assert "Due to check out" in client.get("/today/").text
    client.post(f"/today/{booking.id}/check-out", data={"condition": "GOOD", f"out_tick_{probe.id}": ["0", "1"],
                                                         f"out_qty_{probe.id}": "2", "notes": ""})
    db.session.refresh(booking)
    assert booking.status is BookingStatus.CHECKED_OUT

    client.post(f"/today/{booking.id}/check-in", data={"condition": "GOOD", f"in_tick_{probe.id}": ["0", "1"],
                                                        f"in_qty_{probe.id}": "1", "notes": ""})
    db.session.refresh(booking)
    assert booking.status is BookingStatus.COMPLETED and booking.accessories_missing
    issue = db.session.query(MaintenanceLog).filter_by(kind=LogKind.ISSUE).one()
    assert "Probe ×1" in issue.notes and issue.booking_id == booking.id
    recipients = {m["To"] for m in mailbox if "missing" in m["Subject"].lower()}
    assert recipients == {"s@example.edu", "tech2@example.edu"}
    assert other_tech.id in {n.user_id for n in db.session.query(Notification).filter_by(type="accessories_missing")}
    assert 'id="h-missing"' in client.get("/today/").text

    client.post(f"/today/{booking.id}/resolve-missing")
    db.session.refresh(booking)
    db.session.refresh(issue)
    assert not booking.accessories_missing and issue.resolved_at is not None
    assert 'id="h-missing"' not in client.get("/today/").text


def test_damaged_check_in_takes_unit_out_of_service_acceptance_9(client, make_user, db, scope):
    student = make_user()
    start, end = now_slot(-90)
    booking = add_booking(student, scope, start, end, status=BookingStatus.CHECKED_OUT)
    later = add_booking(make_user(), scope, at(2, 10), at(2, 11))
    as_user(client, make_user(Role.LAB_TECH))
    assert "out of service" in client.get(f"/today/{booking.id}/check-in").text
    client.post(f"/today/{booking.id}/check-in", data={"condition": "DAMAGED", "out_of_service": "1",
                                                        "notes": "Cracked screen"})
    unit = booking.unit
    db.session.refresh(unit)
    db.session.refresh(later)
    assert unit.status is UnitStatus.OUT_OF_SERVICE
    kinds = {e.kind for e in db.session.query(MaintenanceLog).filter_by(unit_id=unit.id)}
    assert {LogKind.ISSUE, LogKind.OUT_OF_SERVICE} <= kinds
    assert later.unit_id == scope.units[1].id  # moved off the broken unit


def test_check_out_window(make_user, db, scope):
    tech = make_user(Role.LAB_TECH)
    booking = add_booking(make_user(), scope, at(2, 10), at(2, 11))
    with pytest.raises(desk.BookingRefused, match="Too early"):
        desk.check_out(booking, tech, desk.Condition.GOOD, "", {})


# --- No-shows and suspension (acceptance #6) ------------------------------------------------------

def test_no_show_job_and_suspension_acceptance_6(client, make_user, db, scope, mailbox):
    student = make_user(email="s@example.edu")
    for minutes_ago in (300, 200, 100):
        start, end = now_slot(-minutes_ago)
        add_booking(student, scope, start, end, unit=scope.units[minutes_ago // 100 % 2])
    assert tick()["detect_no_shows"] == 3
    assert tick()["detect_no_shows"] == 0
    assert {b.status for b in db.session.query(Booking)} == {BookingStatus.NO_SHOW}
    assert db.session.query(NoShowRecord).count() == 3
    db.session.refresh(student)
    assert student.is_suspended
    assert student.suspended_until.astimezone(TZ).date() == (utcnow() + timedelta(days=7)).astimezone(TZ).date()
    assert any("suspended" in m["Subject"].lower() for m in mailbox)

    as_user(client, student)
    form = {"type_id": scope.id, "date": local_day(2).isoformat(), "start": "10:00", "end": "11:00", "unit_id": 0}
    assert "can&#39;t make new bookings" in client.post("/bookings/new", data=form).text


def test_staff_no_shows_have_no_penalty(make_user, db, scope):
    teacher = make_user(Role.TEACHER)
    start, end = now_slot(-60)
    add_booking(teacher, scope, start, end)
    tick()
    assert db.session.query(NoShowRecord).count() == 0


def test_reverse_no_show_lifts_suspension(client, make_user, db, scope):
    student = make_user()
    bookings = []
    for i, minutes_ago in enumerate((300, 200, 100)):
        start, end = now_slot(-minutes_ago, 30)
        bookings.append(add_booking(student, scope, start, end, unit=scope.units[i % 2]))
    tick()
    db.session.refresh(student)
    assert student.is_suspended
    as_user(client, make_user(Role.LAB_TECH))
    client.post(f"/today/{bookings[-1].id}/reverse-no-show")
    db.session.refresh(student)
    db.session.refresh(bookings[-1])
    assert not student.is_suspended
    assert bookings[-1].status is BookingStatus.COMPLETED


def test_manual_no_show(client, make_user, db, scope):
    student = make_user()
    start, end = now_slot(-5)
    booking = add_booking(student, scope, start, end)
    as_user(client, make_user(Role.LAB_TECH))
    client.post(f"/today/{booking.id}/no-show")
    db.session.refresh(booking)
    assert booking.status is BookingStatus.NO_SHOW


# --- Overdue --------------------------------------------------------------------------------------

def test_overdue_job_notifies_once_and_warns_next_booker(make_user, db, scope, mailbox):
    late = make_user(email="late@example.edu")
    nxt = make_user(email="next@example.edu")
    make_user(Role.LAB_TECH, email="tech@example.edu")
    start, end = now_slot(-120, 90)  # ended 30 minutes ago
    add_booking(late, scope, start, end, status=BookingStatus.CHECKED_OUT)
    add_booking(nxt, scope, *now_slot(30))
    assert tick()["detect_overdue"] == 1
    assert tick()["detect_overdue"] == 0
    to = [m["To"] for m in mailbox]
    assert {"late@example.edu", "tech@example.edu", "next@example.edu"} <= set(to)
    assert to.count("late@example.edu") == 1


# --- Out of service and maintenance blocks (acceptance #8) ----------------------------------------

def test_out_of_service_moves_or_cancels_bookings_acceptance_8(client, make_user, db, scope, mailbox):
    osc1, osc2 = scope.units
    a = add_booking(make_user(email="a@example.edu"), scope, at(2, 10), at(2, 11), unit=osc1)
    b = add_booking(make_user(email="b@example.edu"), scope, at(3, 10), at(3, 11), unit=osc1)
    add_booking(make_user(), scope, at(3, 10), at(3, 11), unit=osc2)  # osc2 busy on day 3
    as_user(client, make_user(Role.LAB_TECH))
    response = client.post(f"/units/{osc1.id}/status", data={"status": "OUT_OF_SERVICE", "reason": "Noisy"},
                           follow_redirects=True)
    assert "1 booking moved to another unit and 1 cancelled" in response.text
    db.session.refresh(a)
    db.session.refresh(b)
    assert a.unit_id == osc2.id and a.status is BookingStatus.CONFIRMED
    assert b.status is BookingStatus.CANCELLED
    subjects = {m["To"]: m["Subject"] for m in mailbox}
    assert "moved" in subjects["a@example.edu"].lower() and "cancelled" in subjects["b@example.edu"].lower()
    assert db.session.query(MaintenanceLog).filter_by(kind=LogKind.OUT_OF_SERVICE).count() == 1

    client.post(f"/units/{osc1.id}/status", data={"status": "AVAILABLE", "reason": ""})
    db.session.refresh(osc1)
    assert osc1.status is UnitStatus.AVAILABLE
    assert db.session.query(MaintenanceLog).filter_by(kind=LogKind.BACK_IN_SERVICE).count() == 1


def test_maintenance_block_for_whole_type_cancels(client, make_user, db, scope):
    booking = add_booking(make_user(), scope, at(2, 10), at(2, 11))
    as_user(client, make_user(Role.LAB_TECH))
    client.post(f"/units/{scope.units[0].id}/blocks", data={
        "starts_at": f"{local_day(2).isoformat()}T09:00", "ends_at": f"{local_day(2).isoformat()}T12:00",
        "reason": "Calibration", "scope": "type"})
    assert db.session.query(MaintenanceBlock).count() == 2
    db.session.refresh(booking)
    assert booking.status is BookingStatus.CANCELLED
    page = client.get(f"/equipment/{scope.id}?date={local_day(2).isoformat()}").text
    assert "Calibration" in page


def test_unit_block_moves_booking(client, make_user, db, scope):
    booking = add_booking(make_user(), scope, at(2, 10), at(2, 11), unit=scope.units[0])
    as_user(client, make_user(Role.LAB_TECH))
    client.post(f"/units/{scope.units[0].id}/blocks", data={
        "starts_at": f"{local_day(2).isoformat()}T09:00", "ends_at": f"{local_day(2).isoformat()}T12:00",
        "reason": "Repair", "scope": "unit"})
    db.session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED and booking.unit_id == scope.units[1].id


def test_report_issue_notifies_lab_techs(client, make_user, db, scope):
    student = make_user()
    tech = make_user(Role.LAB_TECH)
    booking = add_booking(student, scope, at(-1, 10), at(-1, 11), status=BookingStatus.COMPLETED)
    as_user(client, student)
    client.post(f"/bookings/{booking.id}/report-issue", data={"notes": "Probe 2 broken"})
    assert db.session.query(MaintenanceLog).filter_by(kind=LogKind.ISSUE, notes="Probe 2 broken").count() == 1
    assert db.session.query(Notification).filter_by(user_id=tech.id, type="issue_reported").count() == 1


# --- Closures (acceptance #15) --------------------------------------------------------------------

def test_holiday_shows_affected_count_then_cancels_acceptance_15(client, make_user, db, scope, mailbox):
    day = local_day(3)
    for i, hh in enumerate((9, 11, 13, 15)):
        add_booking(make_user(email=f"u{i}@example.edu"), scope, at(3, hh), at(3, hh + 1))
    keep = add_booking(make_user(), scope, at(4, 9), at(4, 10))
    as_user(client, make_user(Role.ADMIN))
    data = {"lab_id": 0, "start_date": day.isoformat(), "end_date": "", "reason": "Founders Day"}
    first = client.post("/admin/calendar/holidays/new", data=data)
    assert "4 bookings will be cancelled" in first.text
    assert db.session.query(Booking).filter_by(status=BookingStatus.CANCELLED).count() == 0
    from labbook.models import Holiday

    assert db.session.query(Holiday).count() == 0  # nothing saved until confirmed

    client.post("/admin/calendar/holidays/new", data=data | {"confirm_cancel": "4"})
    assert db.session.query(Booking).filter_by(status=BookingStatus.CANCELLED).count() == 4
    db.session.refresh(keep)
    assert keep.status is BookingStatus.CONFIRMED
    assert len([m for m in mailbox if "lab closed" in m["Subject"].lower()]) == 4
    timeline = client.get(f"/equipment/{scope.id}?date={day.isoformat()}").text
    assert "Closed — Founders Day" in timeline


def test_lab_hours_change_needs_confirmation(client, make_user, db, scope, lab):
    add_booking(make_user(), scope, at(2, 20), at(2, 21))
    as_user(client, make_user(Role.ADMIN))
    data = {"name": lab.name, "location": "", "timezone": "Asia/Kolkata"}
    for prefix in ("term", "vacation"):
        for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            data |= {f"{prefix}_{d}-open": "y", f"{prefix}_{d}-start": "08:00", f"{prefix}_{d}-end": "18:00"}
    assert "1 booking will be cancelled" in client.post(f"/admin/labs/{lab.id}", data=data).text
    db.session.refresh(lab)
    assert lab.term_hours["mon"] == [["00:00", "23:45"]]
    client.post(f"/admin/labs/{lab.id}", data=data | {"confirm_cancel": "1"})
    db.session.refresh(lab)
    assert lab.term_hours["mon"] == [["08:00", "18:00"]]
    assert db.session.query(Booking).one().status is BookingStatus.CANCELLED


# --- CSV export (acceptance #10), audit viewer, pages ---------------------------------------------

def test_csv_export_matches_list_acceptance_10(client, make_user, db, scope):
    user = make_user(name="=HYPERLINK(evil)")
    for offset in (1, 2, 3):
        add_booking(user, scope, at(offset, 10), at(offset, 11))
    add_booking(user, scope, at(2, 12), at(2, 13), status=BookingStatus.CANCELLED)
    add_booking(user, scope, at(20, 10), at(20, 11))  # outside the range
    b = db.session.query(Booking).first()
    b.purpose = "=cmd|' /C calc'!A0"
    db.session.commit()
    as_user(client, make_user(Role.ADMIN))
    params = f"from={local_day(1).isoformat()}&to={local_day(3).isoformat()}"
    page = client.get(f"/bookings/all?{params}").text
    assert 'data-total="4"' in page
    response = client.get(f"/api/export/bookings.csv?{params}")
    assert response.mimetype == "text/csv"
    rows = list(csv.reader(io.StringIO(response.text)))
    assert len(rows) - 1 == 4
    assert any(r[11].startswith("'=") for r in rows[1:])


def test_export_and_list_permissions(client, login_as):
    login_as(Role.TEACHER)
    assert client.get("/bookings/all").status_code == 200
    assert client.get("/api/export/bookings.csv").status_code == 403
    client.post("/logout")
    login_as(Role.STUDENT)
    assert client.get("/bookings/all").status_code == 403


def test_audit_log_viewer(client, make_user, db, scope):
    as_user(client, make_user(Role.ADMIN))
    client.post(f"/units/{scope.units[0].id}/status", data={"status": "OUT_OF_SERVICE", "reason": "x"})
    page = client.get("/admin/audit?entity=EquipmentUnit").text
    assert "EquipmentUnit #" in page and "OUT_OF_SERVICE" in page
    assert db.session.query(AuditLog).filter_by(entity="EquipmentUnit").count() >= 1


def test_staff_pages_render(client, make_user, db, scope):
    student = make_user()
    start, end = now_slot(-30)
    out = add_booking(student, scope, start, end, status=BookingStatus.CHECKED_OUT)
    admin = make_user(Role.ADMIN)
    as_user(client, admin)
    for path in ["/", "/today/", f"/today/{out.id}/check-in", f"/units/{scope.units[0].id}", "/units/",
                 f"/units/{scope.units[0].id}/edit", "/bookings/all", "/admin/audit", f"/bookings/{out.id}"]:
        assert client.get(path).status_code == 200, path
    assert db.session.query(CheckEvent).count() == 0
