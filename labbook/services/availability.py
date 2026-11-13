"""Which units are free when (spec §3.1, FR-B3 rule 7)."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import func, select

from ..extensions import db
from ..models import ACTIVE_STATUSES, Booking, EquipmentType, EquipmentUnit, MaintenanceBlock, UnitStatus
from .calendar import LabDayCache


@dataclass(frozen=True)
class Busy:
    unit_id: int
    starts_at: datetime
    ends_at: datetime
    booking: Booking | None = None
    block: MaintenanceBlock | None = None


def type_timezone(eq_type: EquipmentType) -> ZoneInfo:
    """Times for a type are entered in its labs' timezone (one department, so normally one zone)."""
    for unit in eq_type.units:
        if unit.status is not UnitStatus.RETIRED:
            return ZoneInfo(unit.lab.timezone)
    return ZoneInfo(current_app.config["DEFAULT_TIMEZONE"])


def local_day_bounds(day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tz)
    return start, datetime.combine(day + timedelta(days=1), time.min, tz)


def in_service_units(eq_type: EquipmentType) -> list[EquipmentUnit]:
    return [u for u in eq_type.units if u.status is UnitStatus.AVAILABLE]


def busy_by_unit(unit_ids: list[int], start: datetime, end: datetime,
                 exclude_booking_id: int | None = None) -> dict[int, list[Busy]]:
    """Active bookings and maintenance blocks overlapping [start, end), per unit."""
    result: dict[int, list[Busy]] = defaultdict(list)
    if not unit_ids:
        return result
    stmt = select(Booking).where(
        Booking.unit_id.in_(unit_ids), Booking.status.in_(ACTIVE_STATUSES),
        Booking.starts_at < end, Booking.ends_at > start,
    )
    if exclude_booking_id is not None:
        stmt = stmt.where(Booking.id != exclude_booking_id)
    for booking in db.session.scalars(stmt):
        result[booking.unit_id].append(Busy(booking.unit_id, booking.starts_at, booking.ends_at, booking=booking))
    blocks = select(MaintenanceBlock).where(
        MaintenanceBlock.unit_id.in_(unit_ids), MaintenanceBlock.starts_at < end, MaintenanceBlock.ends_at > start,
    )
    for block in db.session.scalars(blocks):
        result[block.unit_id].append(Busy(block.unit_id, block.starts_at, block.ends_at, block=block))
    for items in result.values():
        items.sort(key=lambda b: b.starts_at)
    return result


def _bookings_that_day(unit_ids: list[int], day_start: datetime, day_end: datetime) -> dict[int, int]:
    rows = db.session.execute(
        select(Booking.unit_id, func.count())
        .where(Booking.unit_id.in_(unit_ids), Booking.status.in_(ACTIVE_STATUSES),
               Booking.starts_at < day_end, Booking.ends_at > day_start)
        .group_by(Booking.unit_id)
    )
    return dict(rows.all())


def free_units(eq_type: EquipmentType, start: datetime, end: datetime, *, unit_id: int | None = None,
               exclude_booking_id: int | None = None, prefer_unit_id: int | None = None,
               lab_days: LabDayCache | None = None) -> list[EquipmentUnit]:
    """In-service units of the type that are open and free for the whole range.

    Ordered for assignment: the preferred unit first (keeps a unit when rescheduling), then the unit with
    the fewest bookings that day to spread wear (spec §3.1), then by asset tag.
    """
    lab_days = lab_days or LabDayCache()
    units = in_service_units(eq_type)
    if unit_id is not None:
        units = [u for u in units if u.id == unit_id]
    tz = type_timezone(eq_type)
    day = start.astimezone(tz).date()
    units = [u for u in units if lab_days.get(u.lab, day).contains(start, end)]
    busy = busy_by_unit([u.id for u in units], start, end, exclude_booking_id)
    units = [u for u in units if not busy.get(u.id)]
    if not units:
        return []
    counts = _bookings_that_day([u.id for u in units], *local_day_bounds(day, tz))
    return sorted(units, key=lambda u: (u.id != prefer_unit_id, counts.get(u.id, 0), u.asset_tag))


@dataclass(frozen=True)
class NowStatus:
    free: int
    total: int
    next_free_at: datetime | None


def now_status(eq_type: EquipmentType, now: datetime) -> NowStatus:
    """For catalogue cards: 'N of M free now' or 'None free now · next at HH:MM' (spec §6.6)."""
    units = in_service_units(eq_type)
    busy = busy_by_unit([u.id for u in units], now, now + timedelta(seconds=1))
    free = sum(1 for u in units if not busy.get(u.id))
    next_free = None
    if units and not free:
        next_free = min(max(b.ends_at for b in busy[u.id]) for u in units if busy.get(u.id))
    return NowStatus(free, len(units), next_free)
