from datetime import timedelta

from conftest import PASSWORD, link_from, login

from labbook.models import AuditLog, Role, User, UserSession
from labbook.services import privacy, settings
from labbook.timeutil import utcnow


def register(client, **overrides):
    data = {"name": "New Student", "email": "new@example.edu", "password": PASSWORD, "confirm": PASSWORD,
            "consent": "y"}
    data.update(overrides)
    return client.post("/register", data=data)


def test_register_requires_privacy_consent(client, db):
    response = register(client, consent="")
    assert response.status_code == 200
    assert "accept the Privacy Notice" in response.text
    assert db.session.query(User).count() == 0


def test_register_creates_student_with_consent_and_sends_verification(client, db, mailbox):
    response = register(client)
    assert response.status_code == 302
    user = db.session.query(User).one()
    assert user.role is Role.STUDENT
    assert user.consent_version == privacy.current_notice().version
    assert not user.is_verified
    assert len(mailbox) == 1 and mailbox[0]["To"] == "new@example.edu"
    assert db.session.query(AuditLog).filter_by(entity="User", action="register").count() == 1

    client.get(link_from(mailbox[0], "/verify/"))
    db.session.refresh(user)
    assert user.is_verified


def test_register_enforces_allowed_domains(client, db):
    settings.set_value("allowed_email_domains", ["rajagiritech.edu.in"], None)
    db.session.commit()
    response = register(client, email="someone@gmail.com")
    assert "@rajagiritech.edu.in" in response.text
    assert db.session.query(User).count() == 0
    assert register(client, email="someone@RajagiriTech.edu.in").status_code == 302


def test_register_rejects_short_password(client, db):
    response = register(client, password="short", confirm="short")
    assert "at least 10 characters" in response.text


def test_login_logout_and_server_side_session(client, make_user, db):
    user = make_user()
    assert login(client, user.email.upper()).status_code == 302
    assert db.session.query(UserSession).filter_by(user_id=user.id).count() == 1
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert db.session.query(UserSession).count() == 0
    assert client.get("/").status_code == 302


def test_wrong_password_rejected(client, make_user):
    user = make_user()
    response = login(client, user.email, "wrong-password-123")
    assert response.status_code == 200
    assert "Incorrect email or password" in response.text


def test_five_failed_logins_block_further_attempts(client, make_user):
    user = make_user()
    for _ in range(5):
        login(client, user.email, "wrong-password-123")
    response = login(client, user.email)  # correct password, but blocked
    assert response.status_code == 429
    assert "Too many attempts" in response.text


def test_deactivated_user_cannot_log_in(client, make_user):
    user = make_user(active=False)
    response = login(client, user.email)
    assert response.status_code == 200
    assert "deactivated" in response.text


def test_idle_session_expires(client, make_user, db):
    user = make_user()
    login(client, user.email)
    row = db.session.query(UserSession).one()
    row.last_seen_at = utcnow() - timedelta(days=8)
    db.session.commit()
    assert client.get("/").status_code == 302


def test_password_reset_link_is_single_use(client, make_user, mailbox, db):
    user = make_user()
    client.post("/forgot-password", data={"email": user.email})
    path = link_from(mailbox[0], "/reset-password/")
    new = "a-brand-new-password"
    assert client.post(path, data={"password": new, "confirm": new}).status_code == 302
    assert login(client, user.email, new).status_code == 302
    client.post("/logout")
    response = client.post(path, data={"password": "another-password-1", "confirm": "another-password-1"})
    assert response.status_code == 302 and "/forgot-password" in response.location
    assert login(client, user.email, new).status_code == 302


def test_forgot_password_does_not_reveal_accounts(client, mailbox):
    response = client.post("/forgot-password", data={"email": "nobody@example.edu"}, follow_redirects=True)
    assert "If an account exists" in response.text
    assert mailbox == []


def test_new_privacy_notice_requires_reconsent(client, login_as, make_user, db):
    admin = make_user(role=Role.ADMIN, email="admin@example.edu")
    login_as()
    privacy.publish(admin, "# New notice\n\nUpdated.")
    db.session.commit()
    response = client.get("/equipment/")
    assert response.status_code == 302 and "/privacy/accept" in response.location
    client.post("/privacy/accept", data={"consent": "y"})
    assert client.get("/equipment/").status_code == 200


def test_change_email_requires_reverification(client, login_as, mailbox, db):
    user = login_as()
    response = client.post("/profile/email", data={"email-new_email": "moved@example.edu",
                                                   "email-current_password": PASSWORD})
    assert response.status_code == 302
    db.session.refresh(user)
    assert user.email == "moved@example.edu" and not user.is_verified
    assert mailbox[-1]["To"] == "moved@example.edu"


def test_change_password_revokes_other_sessions(app, client, login_as, db):
    user = login_as()
    other = app.test_client()
    login(other, user.email)
    assert db.session.query(UserSession).count() == 2
    new = "changed-password-99"
    client.post("/profile/password", data={"pw-current_password": PASSWORD, "pw-password": new, "pw-confirm": new})
    assert db.session.query(UserSession).count() == 1
    assert client.get("/").status_code == 200
    assert other.get("/").status_code == 302
