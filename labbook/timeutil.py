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
