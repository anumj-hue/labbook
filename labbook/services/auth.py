"""Server-side sessions (FR-A6) and auth rate limiting (FR-A5)."""

import hashlib
import secrets
from datetime import timedelta

from flask import current_app, session
from flask_login import login_user, logout_user
from sqlalchemy import delete, or_, select

from ..extensions import db
from ..models import AuthAttempt, User, UserSession
from ..timeutil import utcnow

_SESSION_KEY = "sid"
_TOUCH_INTERVAL = timedelta(minutes=5)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(user: User) -> None:
    """Logs the user in with a fresh server-side session (also prevents session fixation)."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.session.add(UserSession(id=_digest(token), user_id=user.id, created_at=now, last_seen_at=now))
    user.last_active_at = now
    session.clear()
    session.permanent = True
    login_user(user)
    session[_SESSION_KEY] = token


def end_session() -> None:
    token = session.get(_SESSION_KEY)
    if token:
        db.session.execute(delete(UserSession).where(UserSession.id == _digest(token)))
        db.session.commit()
    logout_user()
    session.clear()


def revoke_sessions(user: User, keep_current: bool = False) -> None:
    stmt = delete(UserSession).where(UserSession.user_id == user.id)
    token = session.get(_SESSION_KEY) if keep_current else None
    if token:
        stmt = stmt.where(UserSession.id != _digest(token))
    db.session.execute(stmt)


def load_user(user_id: str) -> User | None:
    """Flask-Login user loader: valid only with a live, unexpired session row."""
    token = session.get(_SESSION_KEY)
    if not token:
        return None
    row = db.session.get(UserSession, _digest(token))
    if row is None or str(row.user_id) != user_id:
        return None
    now = utcnow()
    cfg = current_app.config
    if now - row.last_seen_at > cfg["SESSION_IDLE_TIMEOUT"] or now - row.created_at > cfg["SESSION_ABSOLUTE_TIMEOUT"]:
        db.session.delete(row)
        db.session.commit()
        return None
    user = row.user
    if not user.is_active:
        return None
    if now - row.last_seen_at > _TOUCH_INTERVAL:
        row.last_seen_at = now
        user.last_active_at = now
        db.session.commit()
    return user


def blocked_for(kind: str, ip: str, email: str | None = None) -> int:
    """Seconds until another attempt is allowed, or 0 if not rate limited."""
    limit, window_minutes = current_app.config["AUTH_RATE_LIMITS"][kind]
    window = timedelta(minutes=window_minutes)
    now = utcnow()
    who = [AuthAttempt.ip == ip]
    if email:
        who.append(AuthAttempt.email == email)
    stmt = select(AuthAttempt.created_at).where(
        AuthAttempt.kind == kind, AuthAttempt.created_at > now - window, or_(*who)
    )
    if kind == "login":
        stmt = stmt.where(AuthAttempt.success.is_(False))
    times = sorted(db.session.scalars(stmt))
    if len(times) < limit:
        return 0
    release_at = times[-limit] + window
    return max(1, int((release_at - now).total_seconds()))


def record_attempt(kind: str, ip: str, email: str | None = None, success: bool = False) -> None:
    now = utcnow()
    db.session.execute(delete(AuthAttempt).where(AuthAttempt.created_at < now - timedelta(days=1)))
    db.session.add(AuthAttempt(kind=kind, ip=ip, email=email, success=success, created_at=now))


def wait_message(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    return f"Too many attempts. Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
