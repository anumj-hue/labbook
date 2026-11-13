from datetime import date, datetime

from sqlalchemy import JSON, CheckConstraint, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..timeutil import utcnow
from .types import UTCDateTime

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_LABELS = dict(zip(WEEKDAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), strict=True))


class Lab(db.Model):
    """A room with term and vacation opening hours (spec §3).

    Hours JSON: {"mon": [["08:00", "18:00"]], ...}; a missing or empty day means closed.
    """

    __tablename__ = "lab"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    location: Mapped[str] = mapped_column(String(200), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    term_hours: Mapped[dict] = mapped_column(JSON, default=dict)
    vacation_hours: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    units: Mapped[list["EquipmentUnit"]] = relationship(back_populates="lab")  # noqa: F821

    @property
    def has_term_hours(self) -> bool:
        return any(self.term_hours.get(day) for day in WEEKDAYS)


class Term(db.Model):
    __tablename__ = "term"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="date_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)


class Holiday(db.Model):
    """A closure; lab_id NULL means every lab."""

    __tablename__ = "holiday"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="date_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_id: Mapped[int | None] = mapped_column(ForeignKey("lab.id", ondelete="CASCADE"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(200))

    lab: Mapped[Lab | None] = relationship()
