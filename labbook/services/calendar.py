"""Opening hours per lab and date: term vs vacation hours, holidays and closures (spec §4.16)."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from ..extensions import db
from ..models import WEEKDAYS, Holiday, Lab, Term
from ..rules import LabDay


def term_on(day: date) -> Term | None:
    return db.session.scalar(select(Term).where(Term.start_date <= day, Term.end_date >= day).limit(1))


def holiday_on(lab_id: int, day: date) -> Holiday | None:
    return db.session.scalar(
        select(Holiday)
        .where(Holiday.start_date <= day, Holiday.end_date >= day,
               or_(Holiday.lab_id.is_(None), Holiday.lab_id == lab_id))
        .order_by(Holiday.lab_id.is_(None))  # a lab-specific closure wins over an all-labs one
        .limit(1)
    )


def lab_day(lab: Lab, day: date) -> LabDay:
    holiday = holiday_on(lab.id, day)
    if holiday is not None:
        return LabDay(lab.id, lab.name, (), holiday=holiday.reason)
    tz = ZoneInfo(lab.timezone)
    hours = lab.term_hours if term_on(day) else lab.vacation_hours
    intervals = tuple(
        (datetime.combine(day, time.fromisoformat(start), tz), datetime.combine(day, time.fromisoformat(end), tz))
        for start, end in (hours or {}).get(WEEKDAYS[day.weekday()], [])
    )
    return LabDay(lab.id, lab.name, intervals)


class LabDayCache:
    """Memoises lab_day() for the duration of one request or service call."""

    def __init__(self):
        self._days: dict[tuple[int, date], LabDay] = {}

    def get(self, lab: Lab, day: date) -> LabDay:
        key = (lab.id, day)
        if key not in self._days:
            self._days[key] = lab_day(lab, day)
        return self._days[key]
