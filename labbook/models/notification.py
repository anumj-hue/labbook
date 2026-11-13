from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..timeutil import utcnow
from .types import UTCDateTime
from .user import User


class Notification(db.Model):
    """In-app notification (spec §4.10). payload: {"title", "body", "url"}."""

    __tablename__ = "notification"
    __table_args__ = (Index("ix_notification_user_read", "user_id", "read_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    user: Mapped[User] = relationship()

    @property
    def title(self) -> str:
        return self.payload.get("title", "")

    @property
    def body(self) -> str:
        return self.payload.get("body", "")

    @property
    def url(self) -> str | None:
        return self.payload.get("url")


class JobRun(db.Model):
    """Last run of each scheduled job; lets daily jobs run once a day however often `tick` is called."""

    __tablename__ = "job_run"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(UTCDateTime)
