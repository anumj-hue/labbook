import pytest
from conftest import link_from

from labbook.models import (
    AuditLog,
    EquipmentType,
    EquipmentUnit,
    Lab,
    Role,
    Term,
    UnitStatus,
    User,
    UserSession,
)


@pytest.mark.parametrize(
    ("role", "path", "expected"),
    [
        (Role.STUDENT, "/admin/users", 403),
        (Role.TEACHER, "/admin/settings", 403),
        (Role.LAB_TECH, "/admin/types", 403),
        (Role.STUDENT, "/units/", 403),
        (Role.TEACHER, "/units/", 403),
        (Role.LAB_TECH, "/units/", 200),
        (Role.ADMIN, "/units/", 200),
        (Role.ADMIN, "/admin/users", 200),
    ],
)
def test_role_permissions(login_as, client, role, path, expected):
    login_as(role)
    assert client.get(path).status_code == expected


def test_anonymous_is_redirected_to_login(client):
    response = client.get("/admin/users")
    assert response.status_code == 302 and "/login" in response.location


def _lab_data(**overrides):
    data = {"name": "Electronics Lab", "location": "Block A", "timezone": "Asia/Kolkata",
            "term_mon-open": "y", "term_mon-start": "08:00", "term_mon-end": "18:00",
            "vacation_tue-open": "y", "vacation_tue-start": "09:00", "vacation_tue-end": "13:00"}
    data.update(overrides)
    return data


def test_create_lab_with_hours(login_as, client, db):
    login_as(Role.ADMIN)
    assert client.post("/admin/labs/new", data=_lab_data()).status_code == 302
    lab = db.session.query(Lab).one()
    assert lab.term_hours == {"mon": [["08:00", "18:00"]]}
    assert lab.vacation_hours == {"tue": [["09:00", "13:00"]]}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"term_mon-end": "07:00"}, "Must be after opening time"),
        ({"term_mon-start": "08:10"}, "15-minute steps"),
        ({"timezone": "Mars/Base"}, "Unknown timezone"),
    ],
)
def test_lab_validation(login_as, client, db, overrides, message):
    login_as(Role.ADMIN)
    response = client.post("/admin/labs/new", data=_lab_data(**overrides))
    assert message in response.text
    assert db.session.query(Lab).count() == 0


def test_terms_cannot_overlap(login_as, client, db):
    login_as(Role.ADMIN)
    client.post("/admin/calendar/terms/new", data={"name": "Odd", "start_date": "2026-07-01", "end_date": "2026-11-30"})
    response = client.post("/admin/calendar/terms/new",
                           data={"name": "Even", "start_date": "2026-11-15", "end_date": "2027-04-30"})
    assert "Overlaps" in response.text
    assert db.session.query(Term).count() == 1


def test_equipment_type_unit_and_accessory(login_as, client, db):
    login_as(Role.ADMIN)
    client.post("/admin/labs/new", data=_lab_data())
    response = client.post("/admin/types/new", data={"name": "Oscilloscope", "category": "Scopes",
                                                     "max_duration_min": "120", "requires_approval": "y"})
    assert response.status_code == 302
    eq_type = db.session.query(EquipmentType).one()
    assert eq_type.requires_approval and eq_type.max_duration_min == 120

    client.post(f"/admin/types/{eq_type.id}/accessories", data={"name": "Probe", "expected_qty": "2"})
    assert [a.name for a in eq_type.accessories] == ["Probe"]

    lab = db.session.query(Lab).one()
    unit_data = {"type_id": eq_type.id, "lab_id": lab.id, "asset_tag": "osc-01", "status": "AVAILABLE"}
    assert client.post("/units/new", data=unit_data).status_code == 302
    assert db.session.query(EquipmentUnit).one().asset_tag == "OSC-01"
    response = client.post("/units/new", data=unit_data)
    assert "already has this asset tag" in response.text


def test_max_duration_must_be_multiple_of_15(login_as, client, db):
    login_as(Role.ADMIN)
    response = client.post("/admin/types/new", data={"name": "X", "category": "Y", "max_duration_min": "50"})
    assert "multiple of 15" in response.text


def test_invite_user_and_accept(login_as, client, app, db, mailbox):
    login_as(Role.ADMIN)
    client.post("/admin/users/new", data={"name": "Dr Teach", "email": "teach@example.edu", "role": "TEACHER"})
    user = db.session.query(User).filter_by(email="teach@example.edu").one()
    assert user.role is Role.TEACHER and user.password_hash is None

    invitee = app.test_client()
    path = link_from(mailbox[-1], "/invite/")
    response = invitee.post(path, data={"password": "teacher-pass-1", "confirm": "teacher-pass-1"})
    assert response.status_code == 302
    db.session.refresh(user)
    assert user.is_verified and user.password_hash
    # Invited users accept the privacy notice on first visit.
    assert "/privacy/accept" in invitee.get("/").location
    invitee.post("/privacy/accept", data={"consent": "y"})
    assert invitee.get("/").status_code == 200


def test_role_change_is_audited(login_as, client, make_user, db):
    login_as(Role.ADMIN)
    student = make_user()
    client.post(f"/admin/users/{student.id}", data={"name": student.name, "role": "LAB_TECH"})
    db.session.refresh(student)
    assert student.role is Role.LAB_TECH
    entry = db.session.query(AuditLog).filter_by(entity="User", action="update").one()
    assert entry.before == {"role": "STUDENT"} and entry.after == {"role": "LAB_TECH"}


def test_cannot_demote_last_admin(login_as, client, db):
    admin = login_as(Role.ADMIN)
    response = client.post(f"/admin/users/{admin.id}", data={"name": admin.name, "role": "STUDENT"})
    assert "only active Admin" in response.text
    db.session.refresh(admin)
    assert admin.role is Role.ADMIN


def test_deactivate_revokes_sessions(login_as, client, app, make_user, db):
    login_as(Role.ADMIN)
    student = make_user()
    other = app.test_client()
    other.post("/login", data={"email": student.email, "password": "correct-horse-battery"})
    assert db.session.query(UserSession).filter_by(user_id=student.id).count() == 1
    client.post(f"/admin/users/{student.id}/deactivate")
    db.session.refresh(student)
    assert not student.active
    assert db.session.query(UserSession).filter_by(user_id=student.id).count() == 0
    assert other.get("/").status_code == 302


def test_settings_save_and_audit(login_as, client, db):
    login_as(Role.ADMIN)
    from labbook.services import settings
    from labbook.services.settings import RULES

    data = {rule.key: settings.get(rule.key) for rule in RULES}
    data.update(student_max_active=5, allowed_email_domains="@RajagiriTech.edu.in\nexample.edu")
    assert client.post("/admin/settings", data=data).status_code == 302
    assert settings.get("student_max_active") == 5
    assert settings.get("allowed_email_domains") == ["rajagiritech.edu.in", "example.edu"]
    assert settings.get("rules_reviewed_at") is not None
    assert db.session.query(AuditLog).filter_by(entity="Setting", entity_id="student_max_active").count() == 1


def test_unit_status_change_is_audited(login_as, client, db):
    login_as(Role.LAB_TECH)
    lab = Lab(name="L", term_hours={})
    eq_type = EquipmentType(name="T", category="C")
    db.session.add_all([lab, eq_type])
    db.session.flush()
    unit = EquipmentUnit(type_id=eq_type.id, lab_id=lab.id, asset_tag="T-01")
    db.session.add(unit)
    db.session.commit()
    client.post(f"/units/{unit.id}", data={"type_id": eq_type.id, "lab_id": lab.id, "asset_tag": "T-01",
                                           "status": "OUT_OF_SERVICE"})
    db.session.refresh(unit)
    assert unit.status is UnitStatus.OUT_OF_SERVICE
    entry = db.session.query(AuditLog).filter_by(entity="EquipmentUnit").one()
    assert entry.after == {"status": "OUT_OF_SERVICE"}
