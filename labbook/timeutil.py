from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app


def utcnow() -> datetime:
    return datetime.now(UTC)


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def default_tz() -> ZoneInfo:
    return ZoneInfo(current_app.config["DEFAULT_TIMEZONE"])


def local_today(tz: ZoneInfo | None = None) -> date:
    return utcnow().astimezone(tz or default_tz()).date()


def fmt_date(value: date | datetime | None, with_year: bool = False) -> str:
    """Spec §6.10 style: 'Fri, 13 Nov' (optionally with the year)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.astimezone(default_tz()).date()
    text = f"{value:%a}, {value.day} {value:%b}"
    return f"{text} {value.year}" if with_year else text


def fmt_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    local = value.astimezone(default_tz())
    return f"{fmt_date(local.date(), with_year=True)}, {local:%H:%M}"


def fmt_time(value: datetime | None) -> str:
    """24-hour local time, e.g. '14:30' (spec §6.10)."""
    return f"{value.astimezone(default_tz()):%H:%M}" if value else ""


def fmt_relative(value: datetime | None, now: datetime | None = None) -> str:
    """Relative hints such as 'in 5 min', 'in 2 h', '3 days ago'."""
    if value is None:
        return ""
    now = now or utcnow()
    seconds = (value - now).total_seconds()
    minutes = int(abs(seconds) // 60)
    if minutes < 1:
        return "now"
    if minutes < 60:
        text = f"{minutes} min"
    elif minutes < 48 * 60:
        text = f"{round(minutes / 60)} h"
    else:
        text = f"{round(minutes / 1440)} days"
    return f"in {text}" if seconds >= 0 else f"{text} ago"
