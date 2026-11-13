"""Slice 3: approvals, notifications, jobs, staff cancellation and teacher override."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import login

from labbook.extensions import db as _db
from labbook.jobs import tick
from labbook.models import (
    AuditLog,
    Booking,
    BookingStatus,
    EquipmentType,
    EquipmentUnit,
    Lab,
    Notification,
    Role,
    UnitStatus,
)
from labbook.timeutil import utcnow

TZ = ZoneInfo("Asia/Kolkata")
EVERY_DAY = {d: [["00:00", "23:45"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


def local_day(offset: int):
    return utcnow().astimezone(TZ).date() + timedelta(days=offset)


def at(offset: int, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(local_day(offset), time(hh, mm), TZ)


@pytest.fixture
def lab(db):
    lab = Lab(name="Lab", timezone="Asia/Kolkata", term_hours=EVERY_DAY, vacation_hours=EVERY_DAY)
    db.session.add(lab)
    db.session.commit()
    return lab


def make_type(db, lab, name, units=1, approval=False):
    eq_type = EquipmentType(name=name, category="C", requires_approval=approval)
    db.session.add(eq_type)
    db.session.flush()
    prefix = name[:3].upper()
    db.session.add_all([EquipmentUnit(type_id=eq_type.id, lab_id=lab.id, asset_tag=f"{prefix}-{i:02d}")
                        for i in range(1, units + 1)])
    db.session.commit()
    return eq_type


def form(eq_type, offset=2, start="10:00", end="12:00", **extra):
    return {"type_id": eq_type.id, "date": local_day(offset).isoformat(), "start": start, "end": end,
            "unit_id": 0, "purpose": "Practical"} | extra


def add_booking(user, eq_type, start, end, status=BookingStatus.CONFIRMED, created_at=None, unit=None):
    unit = unit or eq_type.units[0]
    booking = Booking(type_id=eq_type.id, unit_id=unit.id, user_id=user.id, created_by_id=user.id, starts_at=start,
                      ends_at=end, status=status, user_role_at_booking=user.role, created_at=created_at or utcnow())
    _db.session.add(booking)
    _db.session.commit()
    return booking


def switch(client, user):
    client.post("/logout")
    assert login(client, user.email).status_code == 302


# --- Approvals (acceptance #2) --------------------------------------------------------------------

def test_approval_flow_acceptance_2(client, make_user, db, lab, mailbox):
    sa = make_type(db, lab, "Spectrum", approval=True)
    student = make_user(email="s@example.edu")
    teacher = make_user(Role.TEACHER, email="t@example.edu", name="Dr T")
    switch(client, student)

    response = client.post("/bookings/new", data=form(sa), follow_redirects=True)
    assert "Request sent" in response.text
    booking = db.session.query(Booking).one()
    assert booking.status is BookingStatus.PENDING
    assert any(n.type == "new_request" for n in db.session.query(Notification).filter_by(user_id=teacher.id))
    assert [m["To"] for m in mailbox] == ["s@example.edu"]  # request submitted; approvers are in-app only

    # The pending request holds the unit.
    other = make_user()
    switch(client, other)
    assert "No units available" in client.post("/bookings/new", data=form(sa)).text

    switch(client, teacher)
    page = client.get("/approvals/").text
    assert "Spectrum" in page and student.name in page
    client.post(f"/approvals/{booking.id}/approve")
    db.session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED
    assert booking.decided_by_id == teacher.id and booking.decided_at is not None
    assert mailbox[-1]["To"] == "s@example.edu" and "approved" in mailbox[-1]["Subject"].lower()
    assert db.session.query(Notification).filter_by(user_id=student.id, type="request_approved").count() == 1


def test_reject_requires_reason_and_tells_student(client, make_user, db, lab, mailbox):
    sa = make_type(db, lab, "Spectrum", approval=True)
    student = make_user()
    booking = add_booking(student, sa, at(2, 10), at(2, 11), BookingStatus.PENDING)
    switch(client, make_user(Role.TEACHER))
    response = client.post(f"/approvals/{booking.id}/reject", data={"reason": " "}, follow_redirects=True)
    assert "Give a reason" in response.text
    client.post(f"/approvals/{booking.id}/reject", data={"reason": "Book the bench scope instead"})
    db.session.refresh(booking)
    assert booking.status is BookingStatus.REJECTED and booking.decision_reason == "Book the bench scope instead"
    assert "Book the bench scope instead" in mailbox[-1].get_content()


def test_bulk_approve(client, make_user, db, lab):
    sa = make_type(db, lab, "Spectrum", units=2, approval=True)
    a = add_booking(make_user(), sa, at(2, 10), at(2, 11), BookingStatus.PENDING, unit=sa.units[0])
    b = add_booking(make_user(), sa, at(2, 10), at(2, 11), BookingStatus.PENDING, unit=sa.units[1])
    switch(client, make_user(Role.ADMIN))
    client.post("/approvals/bulk-approve", data={"booking_id": [a.id, b.id]})
    assert {x.status for x in db.session.query(Booking)} == {BookingStatus.CONFIRMED}


@pytest.mark.parametrize(("role", "code"), [(Role.STUDENT, 403), (Role.LAB_TECH, 403), (Role.TEACHER, 200),
                                            (Role.ADMIN, 200)])
def test_approvals_access(login_as, client, role, code):
    login_as(role)
    assert client.get("/approvals/").status_code == code


def test_student_edit_sends_confirmed_approval_booking_back_to_pending(client, make_user, db, lab):
    sa = make_type(db, lab, "Spectrum", approval=True)
    student = make_user()
    booking = add_booking(student, sa, at(2, 10), at(2, 11))
    switch(client, student)
    client.post(f"/bookings/{booking.id}/edit", data=form(sa, start="13:00", end="14:00", booking_id=booking.id))
    db.session.refresh(booking)
    assert booking.status is BookingStatus.PENDING and booking.starts_at == at(2, 13)


def test_staff_bookings_of_approval_equipment_are_confirmed(login_as, client, db, lab):
    sa = make_type(db, lab, "Spectrum", approval=True)
    login_as(Role.LAB_TECH)
    client.post("/bookings/new", data=form(sa))
    assert db.session.query(Booking).one().status is BookingStatus.CONFIRMED


# --- Jobs -----------------------------------------------------------------------------------------

def test_expire_pending_job_is_idempotent(make_user, db, lab, mailbox):
    sa = make_type(db, lab, "Spectrum", units=2, approval=True)
    old = add_booking(make_user(), sa, at(5, 10), at(5, 11), BookingStatus.PENDING,
                      created_at=utcnow() - timedelta(hours=49), unit=sa.units[0])
    fresh = add_booking(make_user(), sa, at(5, 10), at(5, 11), BookingStatus.PENDING, unit=sa.units[1])
    assert tick()["expire_pending"] == 1
    assert tick()["expire_pending"] == 0
    db.session.refresh(old)
    db.session.refresh(fresh)
    assert old.status is BookingStatus.EXPIRED and fresh.status is BookingStatus.PENDING
    assert sum("expired" in m["Subject"].lower() for m in mailbox) == 1
    # Start time passing also expires a request.
    assert tick(now=at(5, 10, 1))["expire_pending"] == 1


def test_reminders_sent_once_and_respect_preferences(make_user, db, lab, mailbox):
    scope = make_type(db, lab, "Scope", units=2)
    soon = utcnow() + timedelta(minutes=40)
    keen = make_user(email="keen@example.edu")
    quiet = make_user(email="quiet@example.edu", email_prefs={"reminders": False})
    add_booking(keen, scope, soon, soon + timedelta(hours=1), unit=scope.units[0])
    add_booking(quiet, scope, soon, soon + timedelta(hours=1), unit=scope.units[1])
    assert tick()["send_reminders"] == 2
    assert tick()["send_reminders"] == 0
    assert [m["To"] for m in mailbox] == ["keen@example.edu"]
    assert db.session.query(Notification).filter_by(type="reminder").count() == 2  # in-app for both


def test_daily_digest_runs_once_after_seven(make_user, db, lab, mailbox):
    sa = make_type(db, lab, "Spectrum", approval=True)
    make_user(Role.TEACHER, email="t@example.edu")
    make_user(Role.ADMIN, email="a@example.edu", email_prefs={"digest": False})
    add_booking(make_user(), sa, at(3, 10), at(3, 11), BookingStatus.PENDING)
    morning = at(1, 6, 30)
    assert "daily_digest" not in tick(now=morning)
    assert tick(now=at(1, 7, 5))["daily_digest"] == 2
    assert "daily_digest" not in tick(now=at(1, 9))
    assert tick(now=at(2, 7, 1))["daily_digest"] == 2
    digests = [m for m in mailbox if "waiting for approval" in m["Subject"].lower()]
    assert {m["To"] for m in digests} == {"t@example.edu"}


def test_jobs_api_requires_secret(client):
    assert client.post("/api/jobs/tick").status_code == 404
    assert client.post("/api/jobs/tick", headers={"X-Jobs-Secret": "wrong"}).status_code == 404
    response = client.post("/api/jobs/tick", headers={"X-Jobs-Secret": "test-jobs-secret"})
    assert response.status_code == 200 and "expire_pending" in response.get_json()


def test_jobs_cli(app, make_user, db):
    result = app.test_cli_runner().invoke(args=["jobs", "tick"])
    assert result.exit_code == 0 and "expire_pending: 0" in result.output


# --- Staff cancellation (FR-C4) -------------------------------------------------------------------

def test_lab_tech_cancels_with_reason_and_owner_is_told(client, make_user, db, lab, mailbox):
    scope = make_type(db, lab, "Scope")
    student = make_user(email="s@example.edu")
    booking = add_booking(student, scope, at(2, 10), at(2, 11))

    switch(client, make_user(Role.TEACHER))
    assert client.post(f"/bookings/{booking.id}/cancel", data={"reason": "x"}).status_code == 404

    switch(client, make_user(Role.LAB_TECH))
    assert "Cancel booking" in client.get(f"/bookings/{booking.id}").text
    client.post(f"/bookings/{booking.id}/cancel", data={"reason": ""})
    db.session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED
    client.post(f"/bookings/{booking.id}/cancel", data={"reason": "Unit needs calibration"})
    db.session.refresh(booking)
    assert booking.status is BookingStatus.CANCELLED and not booking.is_late_cancel
    assert mailbox[-1]["To"] == "s@example.edu" and "Unit needs calibration" in mailbox[-1].get_content()


# --- Teacher override (acceptance #7) -------------------------------------------------------------

def test_teacher_override_bumps_most_recent_student_booking_acceptance_7(client, make_user, db, lab, mailbox):
    scope = make_type(db, lab, "Scope", units=2)
    older = add_booking(make_user(email="early@example.edu"), scope, at(3, 10), at(3, 12),
                        created_at=utcnow() - timedelta(days=2), unit=scope.units[0])
    newer = add_booking(make_user(email="late@example.edu"), scope, at(3, 10), at(3, 12),
                        created_at=utcnow() - timedelta(days=1), unit=scope.units[1])
    teacher = make_user(Role.TEACHER, name="Dr T")
    switch(client, teacher)

    preview = client.post("/bookings/validate", data=form(scope, offset=3)).get_json()
    assert not preview["ok"] and preview["override"]["unit"] == "SCO-02"

    no_reason = client.post("/bookings/new", data=form(scope, offset=3, override="1", override_reason=""))
    assert "Give a reason" in no_reason.text

    response = client.post("/bookings/new", data=form(scope, offset=3, override="1",
                                                     override_reason="Exam practical"), follow_redirects=True)
    assert "overridden" in response.text
    db.session.refresh(older)
    db.session.refresh(newer)
    mine = db.session.query(Booking).filter_by(user_id=teacher.id).one()
    assert older.status is BookingStatus.CONFIRMED
    assert newer.status is BookingStatus.BUMPED and newer.bumped_by_booking_id == mine.id
    assert newer.cancel_reason == "Exam practical"
    assert mine.status is BookingStatus.CONFIRMED and mine.unit_id == scope.units[1].id
    bumped_mail = [m for m in mailbox if m["To"] == "late@example.edu"]
    assert bumped_mail and "Exam practical" in bumped_mail[0].get_content()
    assert db.session.query(AuditLog).filter_by(entity="Booking", action="override").count() == 1


def test_no_override_within_minimum_notice_or_for_students(client, make_user, db, lab):
    scope = make_type(db, lab, "Scope")
    soon = utcnow() + timedelta(hours=3)
    soon = soon.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    add_booking(make_user(), scope, soon, soon + timedelta(hours=1))
    local = soon.astimezone(TZ)
    data = {"type_id": scope.id, "date": local.date().isoformat(), "start": f"{local:%H:%M}",
            "end": f"{(local + timedelta(hours=1)):%H:%M}", "unit_id": 0, "purpose": ""}
    switch(client, make_user(Role.TEACHER))
    assert client.post("/bookings/validate", data=data).get_json()["override"] is None

    far = add_booking(make_user(), scope, at(4, 10), at(4, 11))
    switch(client, make_user())
    assert client.post("/bookings/validate", data=form(scope, offset=4, start="10:00", end="11:00")).get_json()[
        "override"] is None
    switch(client, make_user(Role.TEACHER))
    response = client.post("/bookings/new", data=form(scope, offset=4, start="10:00", end="11:00", override="1",
                                                     override_reason="Need it"))
    assert response.status_code == 302
    db.session.refresh(far)
    assert far.status is BookingStatus.BUMPED


def test_teachers_bookings_cannot_be_bumped(client, make_user, db, lab):
    scope = make_type(db, lab, "Scope")
    add_booking(make_user(Role.TEACHER), scope, at(3, 10), at(3, 12))
    switch(client, make_user(Role.TEACHER))
    assert client.post("/bookings/validate", data=form(scope, offset=3)).get_json()["override"] is None


# --- Notifications --------------------------------------------------------------------------------

def test_confirmation_email_and_bell(client, make_user, db, lab, mailbox):
    scope = make_type(db, lab, "Scope")
    student = make_user(email="s@example.edu")
    switch(client, student)
    client.post("/bookings/new", data=form(scope))
    assert mailbox[-1]["Subject"].startswith("Booking confirmed")
    assert "http://labbook.test/bookings/" in mailbox[-1].get_content()
    page = client.get("/notifications/").text
    assert "Booking confirmed" in page and "(unread)" in page

    note = db.session.query(Notification).one()
    assert client.get(f"/notifications/{note.id}").location.startswith("/bookings/")
    db.session.refresh(note)
    assert note.read_at is not None


def test_mark_all_read_and_privacy(client, make_user, db):
    me, other = make_user(), make_user()
    db.session.add_all([Notification(user_id=me.id, type="x", payload={"title": "Mine"}),
                        Notification(user_id=other.id, type="x", payload={"title": "Theirs"})])
    db.session.commit()
    switch(client, me)
    assert "Theirs" not in client.get("/notifications/").text
    theirs = db.session.query(Notification).filter_by(user_id=other.id).one()
    assert client.get(f"/notifications/{theirs.id}").status_code == 404
    client.post("/notifications/read-all")
    assert db.session.query(Notification).filter_by(user_id=me.id, read_at=None).count() == 0
    assert db.session.query(Notification).filter_by(user_id=other.id, read_at=None).count() == 1


def test_refused_booking_sends_no_email(client, make_user, db, lab, mailbox):
    scope = make_type(db, lab, "Scope")
    switch(client, make_user())
    client.post("/bookings/new", data=form(scope, start="10:00", end="16:00"))
    assert mailbox == [] and db.session.query(Notification).count() == 0


def test_unread_badge_in_nav(client, make_user, db, lab):
    sa = make_type(db, lab, "Spectrum", approval=True)
    add_booking(make_user(), sa, at(2, 10), at(2, 11), BookingStatus.PENDING)
    teacher = make_user(Role.TEACHER)
    switch(client, teacher)
    assert "Approvals" in client.get("/").text and "(1)" in client.get("/").text


def test_retired_units_never_offered_for_override(client, make_user, db, lab):
    scope = make_type(db, lab, "Scope", units=2)
    add_booking(make_user(), scope, at(3, 10), at(3, 12), unit=scope.units[0])
    scope.units[1].status = UnitStatus.RETIRED
    db.session.commit()
    switch(client, make_user(Role.TEACHER))
    payload = client.post("/bookings/validate", data=form(scope, offset=3)).get_json()
    assert payload["override"]["unit"] == "SCO-01"
