from datetime import datetime
from enum import StrEnum

from flask_login import UserMixin
from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..timeutil import utcnow
from .types import UTCDateTime


class Role(StrEnum):
    STUDENT = "STUDENT"
    TEACHER = "TEACHER"
    LAB_TECH = "LAB_TECH"
    ADMIN = "ADMIN"

    @property
    def label(self) -> str:
        return {"STUDENT": "Student", "TEACHER": "Teacher", "LAB_TECH": "Lab Tech", "ADMIN": "Admin"}[self.value]

    @property
    def is_staff(self) -> bool:
        return self is not Role.STUDENT


# Non-critical emails users may switch off (spec §4.10, marked †).
EMAIL_PREF_DEFAULTS = {"reminders": True, "digest": True, "waitlist_updates": True}


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254, collation="NOCASE"), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="role", native_enum=False, length=16, create_constraint=True, validate_strings=True),
        default=Role.STUDENT,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    suspended_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    email_prefs: Mapped[dict] = mapped_column(JSON, default=lambda: dict(EMAIL_PREF_DEFAULTS))
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consent_version: Mapped[int | None] = mapped_column(Integer)
    last_active_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    anonymised_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_active(self) -> bool:  # Flask-Login refuses to log in inactive users
        return self.active and self.anonymised_at is None

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def is_suspended(self) -> bool:
        return self.suspended_until is not None and self.suspended_until > utcnow()

    @property
    def initials(self) -> str:
        parts = [p for p in self.name.replace(".", " ").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"

    def has_role(self, *roles: Role) -> bool:
        """Admins can do anything the other roles can (spec §2)."""
        return self.role is Role.ADMIN or self.role in roles

    def email_pref(self, key: str) -> bool:
        return (self.email_prefs or {}).get(key, EMAIL_PREF_DEFAULTS[key])

    def __repr__(self) -> str:
        return f"<User {self.id} {self.role}>"


class UserSession(db.Model):
    """Server-side session row (FR-A6). `id` is a SHA-256 digest of the token held in the cookie."""

    __tablename__ = "user_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")


class AuthAttempt(db.Model):
    """Auth endpoint attempts, used for rate limiting (FR-A5). Rows older than a day are purged."""

    __tablename__ = "auth_attempt"
    __table_args__ = (Index("ix_auth_attempt_kind_created", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(254, collation="NOCASE"))
    ip: Mapped[str] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PrivacyNotice(db.Model):
    __tablename__ = "privacy_notice"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    body_md: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    published_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))

    published_by: Mapped[User | None] = relationship()
