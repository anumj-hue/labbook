"""Booking list and CSV export for staff (spec FR-R6, acceptance #10).

The list page and the CSV share bookings_query(), so the exported row count always matches the list.
"""

import csv
import io
from datetime import date, datetime, time, timedelta

from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload

from ..models import Booking, BookingStatus, EquipmentUnit
from ..timeutil import default_tz

CSV_COLUMNS = ("id", "starts_at", "ends_at", "duration_min", "equipment", "unit", "lab", "booker", "role", "status",
               "late_cancel", "purpose", "created_at")


def bookings_query(start_day: date, end_day: date, status: BookingStatus | None = None,
                   type_id: int | None = None) -> Select:
    """Bookings starting between start_day and end_day inclusive (lab-local dates), all statuses by default."""
    tz = default_tz()
    stmt = (
        select(Booking)
        .where(Booking.starts_at >= datetime.combine(start_day, time.min, tz),
               Booking.starts_at < datetime.combine(end_day + timedelta(days=1), time.min, tz))
        .options(selectinload(Booking.unit).selectinload(EquipmentUnit.lab), selectinload(Booking.type),
                 selectinload(Booking.user))
        .order_by(Booking.starts_at, Booking.id)
    )
    if status is not None:
        stmt = stmt.where(Booking.status == status)
    if type_id:
        stmt = stmt.where(Booking.type_id == type_id)
    return stmt


def _local(value: datetime) -> str:
    return value.astimezone(default_tz()).strftime("%Y-%m-%d %H:%M")


def bookings_csv(bookings: list[Booking]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for b in bookings:
        writer.writerow([
            b.id, _local(b.starts_at), _local(b.ends_at), b.duration_minutes, b.type.name, b.unit.asset_tag,
            b.unit.lab.name, b.user.name if b.user else "Anonymised user", b.user_role_at_booking.label,
            b.status.label, "yes" if b.is_late_cancel else "", _csv_safe(b.purpose), _local(b.created_at),
        ])
    return out.getvalue()


def _csv_safe(text: str) -> str:
    """Neutralise spreadsheet formula injection in free text."""
    return "'" + text if text and text[0] in "=+-@\t\r" else text


def week_stats(today: date) -> dict:
    """Admin home (spec §6.4): this ISO week's bookings, utilisation, no-shows and pending approvals."""
    from sqlalchemy import func

    from ..extensions import db
    from ..models import EquipmentType, UnitStatus
    from .calendar import LabDayCache

    tz = default_tz()
    monday = today - timedelta(days=today.weekday())
    start = datetime.combine(monday, time.min, tz)
    end = start + timedelta(days=7)
    rows = db.session.scalars(select(Booking).where(Booking.starts_at >= start, Booking.starts_at < end)).all()
    used = (BookingStatus.CONFIRMED, BookingStatus.CHECKED_OUT, BookingStatus.COMPLETED)
    booked_minutes = sum(b.duration_minutes for b in rows if b.status in used)
    cache = LabDayCache()
    available_minutes = 0
    for eq_type in db.session.scalars(select(EquipmentType).where(EquipmentType.is_archived.is_(False))):
        for unit in eq_type.units:
            if unit.status is not UnitStatus.AVAILABLE:
                continue
            for offset in range(7):
                for a, b in cache.get(unit.lab, monday + timedelta(days=offset)).intervals:
                    available_minutes += int((b - a).total_seconds() // 60)
    return {
        "bookings": sum(1 for b in rows if b.status in (*used, BookingStatus.PENDING, BookingStatus.NO_SHOW)),
        "no_shows": sum(1 for b in rows if b.status is BookingStatus.NO_SHOW),
        "utilisation": round(100 * booked_minutes / available_minutes) if available_minutes else 0,
        "pending": db.session.scalar(select(func.count()).select_from(Booking)
                                     .where(Booking.status == BookingStatus.PENDING)),
    }
