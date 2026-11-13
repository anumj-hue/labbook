"""Account lifecycle: registration, verification, invites, role changes, deactivation."""

from sqlalchemy import func, select

from ..extensions import db
from ..models import Role, User
from ..timeutil import utcnow
from . import audit, privacy, settings
from .auth import revoke_sessions
from .mail import external_url, send_mail
from .passwords import hash_password
from .tokens import make_token

USER_AUDIT_FIELDS = ("name", "email", "role", "active", "suspended_until")


class UserError(Exception):
    """A user-facing reason an account operation was refused."""


def normalise_email(email: str) -> str:
    return email.strip().lower()


def find_by_email(email: str) -> User | None:
    return db.session.scalar(select(User).where(User.email == normalise_email(email)))


def allowed_domains() -> list[str]:
    return list(settings.get("allowed_email_domains") or [])


def email_domain_allowed(email: str) -> bool:
    domains = allowed_domains()
    return not domains or email.rsplit("@", 1)[-1].lower() in domains


def domain_error_message() -> str:
    domains = allowed_domains()
    listed = ", ".join(f"@{d}" for d in domains)
    return f"Use your institution email address ({listed})."


def register(name: str, email: str, password: str) -> User:
    email = normalise_email(email)
    if not email_domain_allowed(email):
        raise UserError(domain_error_message())
    if find_by_email(email):
        raise UserError("An account with this email already exists. Sign in or reset your password.")
    notice = privacy.current_notice()
    now = utcnow()
    user = User(
        name=name.strip(),
        email=email,
        password_hash=hash_password(password),
        role=Role.STUDENT,  # self-registration is always Student (spec §2)
        consent_at=now,
        consent_version=notice.version,
    )
    db.session.add(user)
    db.session.flush()
    audit.record(None, "User", user.id, "register", None, audit.snapshot(user, *USER_AUDIT_FIELDS))
    return user


def send_verification(user: User) -> bool:
    link = external_url("auth.verify_email", token=make_token(user, "verify"))
    return send_mail(user.email, "Verify your LabBook email", "verify", user=user, link=link)


def send_password_reset(user: User) -> bool:
    link = external_url("auth.reset_password", token=make_token(user, "reset"))
    return send_mail(user.email, "Reset your LabBook password", "reset", user=user, link=link)


def send_invite(user: User) -> bool:
    link = external_url("auth.accept_invite", token=make_token(user, "invite"))
    return send_mail(user.email, "You're invited to LabBook", "invite", user=user, link=link)


def mark_verified(user: User) -> None:
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()


def set_password(user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    revoke_sessions(user)


def invite(actor: User, name: str, email: str, role: Role) -> User:
    email = normalise_email(email)
    if find_by_email(email):
        raise UserError("A user with this email already exists.")
    user = User(name=name.strip(), email=email, role=role, password_hash=None)
    db.session.add(user)
    db.session.flush()
    audit.record(actor, "User", user.id, "invite", None, audit.snapshot(user, *USER_AUDIT_FIELDS))
    return user


def _active_admin_count() -> int:
    return db.session.scalar(
        select(func.count()).select_from(User).where(User.role == Role.ADMIN, User.active.is_(True))
    )


def _guard_last_admin(user: User) -> None:
    if user.role is Role.ADMIN and user.active and _active_admin_count() <= 1:
        raise UserError("This is the only active Admin. Promote another user to Admin first.")


def update(actor: User, user: User, name: str, role: Role) -> None:
    before = audit.snapshot(user, *USER_AUDIT_FIELDS)
    if role is not user.role:
        _guard_last_admin(user)
    user.name = name.strip()
    user.role = role
    audit.record(actor, "User", user.id, "update", before, audit.snapshot(user, *USER_AUDIT_FIELDS))


def deactivate(actor: User, user: User) -> None:
    """FR-A7. Cancelling the user's future bookings is added with bookings (slice 2)."""
    if user.id == actor.id:
        raise UserError("You can't deactivate your own account.")
    _guard_last_admin(user)
    before = audit.snapshot(user, *USER_AUDIT_FIELDS)
    user.active = False
    revoke_sessions(user)
    audit.record(actor, "User", user.id, "deactivate", before, audit.snapshot(user, *USER_AUDIT_FIELDS))


def reactivate(actor: User, user: User) -> None:
    before = audit.snapshot(user, *USER_AUDIT_FIELDS)
    user.active = True
    audit.record(actor, "User", user.id, "reactivate", before, audit.snapshot(user, *USER_AUDIT_FIELDS))


def lift_suspension(actor: User, user: User) -> None:
    before = audit.snapshot(user, *USER_AUDIT_FIELDS)
    user.suspended_until = None
    audit.record(actor, "User", user.id, "lift_suspension", before, audit.snapshot(user, *USER_AUDIT_FIELDS))
