"""Signed, expiring email tokens (FR-A2, FR-A3, FR-A7).

Reset and invite tokens embed a fingerprint of the current password hash, so they stop working
as soon as a password is set: that makes them single-use without a token table.
"""

import hashlib

from flask import current_app
from itsdangerous import BadSignature, URLSafeTimedSerializer

from ..extensions import db
from ..models import User

MAX_AGE_SECONDS = {
    "verify": 24 * 3600,
    "reset": 3600,
    "invite": 72 * 3600,
}


def _serializer(purpose: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=f"labbook.{purpose}")


def _password_fingerprint(user: User) -> str:
    return hashlib.sha256((user.password_hash or "").encode()).hexdigest()[:16]


def make_token(user: User, purpose: str) -> str:
    if purpose not in MAX_AGE_SECONDS:
        raise ValueError(purpose)
    payload = {"uid": user.id, "email": user.email.lower()}
    if purpose in ("reset", "invite"):
        payload["pw"] = _password_fingerprint(user)
    return _serializer(purpose).dumps(payload)


def load_token(token: str, purpose: str) -> User | None:
    try:
        data = _serializer(purpose).loads(token, max_age=MAX_AGE_SECONDS[purpose])
    except BadSignature:
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or user.email.lower() != data.get("email"):
        return None
    if "pw" in data and data["pw"] != _password_fingerprint(user):
        return None
    return user
