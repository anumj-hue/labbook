from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from ..timeutil import utcnow
from .lab import Lab
from .types import UTCDateTime


class UnitStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    RETIRED = "RETIRED"

    @property
    def label(self) -> str:
        return {"AVAILABLE": "Available", "OUT_OF_SERVICE": "Out of service", "RETIRED": "Retired"}[self.value]


class EquipmentType(db.Model):
    __tablename__ = "equipment_type"
    __table_args__ = (
        CheckConstraint(
            "max_duration_min IS NULL OR (max_duration_min > 0 AND max_duration_min % 15 = 0)",
            name="max_duration_step",
        ),
        CheckConstraint("student_advance_days IS NULL OR student_advance_days >= 0", name="advance_days"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    safety_notes: Mapped[str] = mapped_column(Text, default="")
    photo_url: Mapped[str | None] = mapped_column(String(500))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    purpose_required: Mapped[bool] = mapped_column(Boolean, default=False)
    max_duration_min: Mapped[int | None] = mapped_column(Integer)  # NULL -> global setting
    student_advance_days: Mapped[int | None] = mapped_column(Integer)  # NULL -> global setting
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    units: Mapped[list["EquipmentUnit"]] = relationship(back_populates="type", order_by="EquipmentUnit.asset_tag")
    accessories: Mapped[list["Accessory"]] = relationship(
        back_populates="type", order_by=lambda: (Accessory.sort_order, Accessory.id)
    )

    @property
    def units_in_service(self) -> list["EquipmentUnit"]:
        return [u for u in self.units if u.status is UnitStatus.AVAILABLE]

    @property
    def active_accessories(self) -> list["Accessory"]:
        return [a for a in self.accessories if not a.is_archived]

    @property
    def labs(self) -> list[Lab]:
        labs = {u.lab.id: u.lab for u in self.units if u.status is not UnitStatus.RETIRED}
        return sorted(labs.values(), key=lambda lab: lab.name)


class Accessory(db.Model):
    __tablename__ = "accessory"
    __table_args__ = (CheckConstraint("expected_qty >= 1", name="qty_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("equipment_type.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    expected_qty: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    type: Mapped[EquipmentType] = relationship(back_populates="accessories")


class EquipmentUnit(db.Model):
    __tablename__ = "equipment_unit"

    id: Mapped[int] = mapped_column(primary_key=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("equipment_type.id"), index=True)
    lab_id: Mapped[int] = mapped_column(ForeignKey("lab.id"), index=True)
    asset_tag: Mapped[str] = mapped_column(String(40, collation="NOCASE"), unique=True)
    serial_no: Mapped[str] = mapped_column(String(80), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[UnitStatus] = mapped_column(
        Enum(UnitStatus, name="unit_status", native_enum=False, length=16, create_constraint=True,
             validate_strings=True),
        default=UnitStatus.AVAILABLE,
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    type: Mapped[EquipmentType] = relationship(back_populates="units")
    lab: Mapped[Lab] = relationship(back_populates="units")
