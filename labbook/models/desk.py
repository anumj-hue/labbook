"""Check-out/in records, maintenance log and no-show records (spec §4.8, §4.9, §4.15)."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Enum, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..timeutil import utcnow
from .booking import Booking
from .equipment import Accessory, EquipmentUnit
from .types import UTCDateTime
from .user import User


class CheckKind(StrEnum):
    OUT = "OUT"
    IN = "IN"


class Condition(StrEnum):
    GOOD = "GOOD"
    MINOR = "MINOR"
    DAMAGED = "DAMAGED"

    @property
    def label(self) -> str:
        return {"GOOD": "Good", "MINOR": "Minor issue", "DAMAGED": "Damaged"}[self.value]


class LogKind(StrEnum):
    ISSUE = "ISSUE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    REPAIR = "REPAIR"
    CALIBRATION = "CALIBRATION"
    BACK_IN_SERVICE = "BACK_IN_SERVICE"

    @property
    def label(self) -> str:
        return {"ISSUE": "Issue", "OUT_OF_SERVICE": "Out of service", "REPAIR": "Repair",
                "CALIBRATION": "Calibration", "BACK_IN_SERVICE": "Back in service"}[self.value]


def _enum(cls, name):
    return Enum(cls, name=name, native_enum=False, length=16, create_constraint=True, validate_strings=True)


class CheckEvent(db.Model):
    __tablename__ = "check_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id", ondelete="CASCADE"), index=True)
    kind: Mapped[CheckKind] = mapped_column(_enum(CheckKind, "check_kind"))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    condition: Mapped[Condition] = mapped_column(_enum(Condition, "condition"))
    notes: Mapped[str] = mapped_column(Text, default="")

    booking: Mapped[Booking] = relationship()
    staff: Mapped[User | None] = relationship()
    items: Mapped[list["CheckAccessory"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class CheckAccessory(db.Model):
    __tablename__ = "check_accessory"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_event_id: Mapped[int] = mapped_column(ForeignKey("check_event.id", ondelete="CASCADE"), index=True)
    accessory_id: Mapped[int] = mapped_column(ForeignKey("accessory.id"))
    qty: Mapped[int] = mapped_column(Integer)
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))

    event: Mapped[CheckEvent] = relationship(back_populates="items")
    accessory: Mapped[Accessory] = relationship()


class MaintenanceLog(db.Model):
    __tablename__ = "maintenance_log"
    __table_args__ = (Index("ix_maintenance_log_unit_created", "unit_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("equipment_unit.id"))
    kind: Mapped[LogKind] = mapped_column(_enum(LogKind, "log_kind"))
    notes: Mapped[str] = mapped_column(Text, default="")
    reported_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    booking_id: Mapped[int | None] = mapped_column(ForeignKey("booking.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    unit: Mapped[EquipmentUnit] = relationship()
    reported_by: Mapped[User | None] = relationship()
    booking: Mapped[Booking | None] = relationship()

    @property
    def is_open_issue(self) -> bool:
        return self.kind is LogKind.ISSUE and self.resolved_at is None


class NoShowRecord(db.Model):
    __tablename__ = "no_show_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"), index=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id", ondelete="CASCADE"), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    reversed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    marked_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    caused_suspension: Mapped[bool] = mapped_column(Boolean, default=False)

    booking: Mapped[Booking] = relationship()
