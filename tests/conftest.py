import re

import pytest
from flask.globals import _cv_app
from flask.testing import FlaskClient

from labbook import create_app
from labbook.extensions import db as _db
from labbook.models import Role, User
from labbook.services import privacy
from labbook.services.mail import outbox
from labbook.services.passwords import hash_password
from labbook.timeutil import utcnow

PASSWORD = "correct-horse-battery"


class IsolatedClient(FlaskClient):
    """Runs each request in its own app context, as in production.

    Tests keep an app context pushed for direct DB access; without this, requests would reuse it and
    share `g` (Flask-Login's cached user) and the SQLAlchemy session across requests.
    """

    def open(self, *args, **kwargs):
        token = _cv_app.set(None)
        try:
            return super().open(*args, **kwargs)
        finally:
            _cv_app.reset(token)


@pytest.fixture
def app(tmp_path):
    app = create_app("testing", SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}")
    app.test_client_class = IsolatedClient
    with app.app_context():
        _db.create_all()
        privacy.current_notice()
        _db.session.commit()
        yield app
        _db.session.remove()
        _db.engine.dispose()


@pytest.fixture
def db(app):
    return _db


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def mailbox(app):
    box = outbox()
    box.clear()
    return box


@pytest.fixture
def make_user(db):
    def factory(role=Role.STUDENT, email=None, name="Test User", verified=True, password=PASSWORD, **kwargs):
        factory.n += 1
        notice = privacy.current_notice()
        user = User(
            name=name,
            email=email or f"user{factory.n}@example.edu",
            role=role,
            password_hash=hash_password(password) if password else None,
            email_verified_at=utcnow() if verified else None,
            consent_at=utcnow(),
            consent_version=notice.version,
            **kwargs,
        )
        db.session.add(user)
        db.session.commit()
        return user

    factory.n = 0
    return factory


def login(client, email, password=PASSWORD):
    return client.post("/login", data={"email": email, "password": password})


@pytest.fixture
def login_as(client, make_user):
    def do(role=Role.STUDENT, **kwargs):
        user = make_user(role=role, **kwargs)
        response = login(client, user.email)
        assert response.status_code == 302, response.text
        return user

    return do


def link_from(message, path_prefix: str) -> str:
    body = message.get_content()
    match = re.search(rf"http://labbook\.test({re.escape(path_prefix)}\S+)", body)
    assert match, body
    return match.group(1)
