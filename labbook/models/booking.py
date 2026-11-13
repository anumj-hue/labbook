from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..timeutil import utcnow
from .equipment import EquipmentType, EquipmentUnit
from .types import UTCDateTime
from .user import Role, User


class BookingStatus(StrEnum):
    OFFERED = "OFFERED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CHECKED_OUT = "CHECKED_OUT"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    NO_SHOW = "NO_SHOW"
    BUMPED = "BUMPED"
    LAPSED = "LAPSED"

    @property
    def label(self) -> str:
        return {
            "OFFERED": "Offered", "PENDING": "Pending", "CONFIRMED": "Confirmed", "CHECKED_OUT": "Checked out",
            "COMPLETED": "Completed", "CANCELLED": "Cancelled", "REJECTED": "Rejected", "EXPIRED": "Expired",
            "NO_SHOW": "No-show", "BUMPED": "Bumped", "LAPSED": "Lapsed",
        }[self.value]

    @property
    def is_active(self) -> bool:
        return self in ACTIVE_STATUSES


# The statuses that block a unit's time slot (spec §5). The overlap triggers are generated from this
# tuple (models/triggers.py), so keep it the single source of truth.
ACTIVE_STATUSES: tuple[BookingStatus, ...] = (
    BookingStatus.OFFERED,
    BookingStatus.PENDING,
    BookingStatus.CONFIRMED,
    BookingStatus.CHECKED_OUT,
)

# Statuses that count towards a student's "active bookings" quota (spec §4.6).
QUOTA_STATUSES: tuple[BookingStatus, ...] = (BookingStatus.PENDING, BookingStatus.CONFIRMED)

# Statuses whose time counts towards a student's weekly booked hours.
HOURS_STATUSES: tuple[BookingStatus, ...] = (
    BookingStatus.PENDING,
    BookingStatus.CONFIRMED,
    BookingStatus.CHECKED_OUT,
    BookingStatus.COMPLETED,
)


class Booking(db.Model):
    __tablename__ = "booking"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="time_order"),
        Index("ix_booking_unit_starts", "unit_id", "starts_at"),
        Index("ix_booking_user_starts", "user_id", "starts_at"),
        Index("ix_booking_status_starts", "status", "starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("equipment_unit.id"))
    type_id: Mapped[int] = mapped_column(ForeignKey("equipment_type.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))  # NULL once anonymised
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status", native_enum=False, length=16, create_constraint=True,
             validate_strings=True),
    )
    user_role_at_booking: Mapped[Role] = mapped_column(
        Enum(Role, name="booking_role", native_enum=False, length=16, create_constraint=True, validate_strings=True),
    )
    purpose: Mapped[str] = mapped_column(Text, default="")
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    cancelled_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    is_late_cancel: Mapped[bool] = mapped_column(Boolean, default=False)
    bumped_by_booking_id: Mapped[int | None] = mapped_column(ForeignKey("booking.id", ondelete="SET NULL"))
    reminded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    unit: Mapped[EquipmentUnit] = relationship()
    type: Mapped[EquipmentType] = relationship()
    user: Mapped[User | None] = relationship(foreign_keys=[user_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    cancelled_by: Mapped[User | None] = relationship(foreign_keys=[cancelled_by_id])
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_id])

    @property
    def duration_minutes(self) -> int:
        return int((self.ends_at - self.starts_at).total_seconds() // 60)

    @property
    def has_started(self) -> bool:
        return self.starts_at <= utcnow()

    @property
    def can_change(self) -> bool:
        """Owner may edit or cancel before it starts (FR-C1, FR-C3)."""
        return self.status in QUOTA_STATUSES and not self.has_started


class MaintenanceBlock(db.Model):
    """A time range when a unit can't be booked (spec §3). Managed from the Units pages in slice 4."""

    __tablename__ = "maintenance_block"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="time_order"),
        Index("ix_maintenance_block_unit_starts", "unit_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("equipment_unit.id"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime)
    reason: Mapped[str] = mapped_column(String(200))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    unit: Mapped[EquipmentUnit] = relationship()
