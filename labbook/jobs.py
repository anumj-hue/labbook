"""Scheduled jobs (spec §7.6), run by `flask jobs tick` every minute (cron / Task Scheduler) or by
POST /api/jobs/tick with the X-Jobs-Secret header.

Every job is idempotent: running tick twice in a row changes nothing the second time. Each job commits
on its own, so one failing job doesn't undo the others.
"""

import hmac
import logging
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

import click
from flask import Flask, current_app
from flask.cli import AppGroup
from sqlalchemy import func, select

from .extensions import db
from .models import Booking, BookingStatus, JobRun
from .services import bookings, desk, notify
from .timeutil import default_tz, utcnow

log = logging.getLogger(__name__)

REMINDER_LEAD = timedelta(minutes=60)


@dataclass(frozen=True)
class Job:
    name: str
    run: Callable[[datetime], int]
    daily_at: time | None = None  # None = every tick


def expire_pending(now: datetime) -> int:
    return bookings.expire_due(now)


def send_reminders(now: datetime) -> int:
    """Confirmed bookings starting within the next hour that haven't been reminded yet."""
    due = db.session.scalars(
        select(Booking).where(Booking.status == BookingStatus.CONFIRMED, Booking.reminded_at.is_(None),
                              Booking.starts_at > now, Booking.starts_at <= now + REMINDER_LEAD)
    ).all()
    for booking in due:
        booking.reminded_at = now
        notify.notify(booking.user, notify.REMINDER, "Your booking starts soon",
                      f"{booking.type.name} ({booking.unit.asset_tag}) is booked for you on {notify.when(booking)} "
                      f"in {booking.unit.lab.name}.", notify.booking_path(booking))
    return len(due)


def daily_digest(now: datetime) -> int:
    """07:00 summary of pending approvals for Teachers and Admins (email only, spec §4.10 †)."""
    pending = db.session.scalar(
        select(func.count()).select_from(Booking).where(Booking.status == BookingStatus.PENDING)
    )
    if not pending:
        return 0
    oldest = db.session.scalars(
        select(Booking).where(Booking.status == BookingStatus.PENDING).order_by(Booking.created_at).limit(5)
    ).all()
    lines = [f"{pending} booking request{'s are' if pending != 1 else ' is'} waiting for approval."]
    lines += [f"- {b.user.name if b.user else 'Unknown'}: {b.type.name}, {notify.when(b)}" for b in oldest]
    sent = 0
    for approver in notify.approvers():
        notify.notify(approver, notify.DIGEST, "Requests waiting for approval", "\n".join(lines),
                      notify.url_path("approvals.index"), in_app=False)
        sent += 1
    return sent


JOBS: tuple[Job, ...] = (
    Job("expire_pending", expire_pending),
    Job("detect_no_shows", desk.detect_no_shows),
    Job("detect_overdue", desk.detect_overdue),
    Job("send_reminders", send_reminders),
    Job("daily_digest", daily_digest, daily_at=time(7, 0)),
)


def _due(job: Job, now: datetime) -> bool:
    if job.daily_at is None:
        return True
    tz = default_tz()
    local = now.astimezone(tz)
    if local.time() < job.daily_at:
        return False
    last = db.session.get(JobRun, job.name)
    return last is None or last.last_run_at.astimezone(tz).date() < local.date()


def tick(now: datetime | None = None) -> dict[str, int]:
    """Runs every due job once; returns how many items each one handled."""
    now = now or utcnow()
    results: dict[str, int] = {}
    for job in JOBS:
        try:
            if not _due(job, now):
                continue
            results[job.name] = job.run(now)
            run = db.session.get(JobRun, job.name) or JobRun(name=job.name)
            run.last_run_at = now
            db.session.add(run)
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("Job %s failed", job.name)
            results[job.name] = -1
    return results


# --- CLI ------------------------------------------------------------------------------------------

jobs_cli = AppGroup("jobs", help="Scheduled jobs (reminders, expiry, digest).")


@jobs_cli.command("tick")
def tick_command() -> None:
    """Run every due job once. Schedule this every minute."""
    for name, count in tick().items():
        click.echo(f"{name}: {'FAILED' if count < 0 else count}")


@jobs_cli.command("run")
@click.option("--interval", default=60, show_default=True, help="Seconds between ticks.")
def run_command(interval: int) -> None:
    """Development helper: tick forever."""
    click.echo(f"Running jobs every {interval}s. Ctrl+C to stop.")
    while True:
        results = {k: v for k, v in tick().items() if v}
        if results:
            click.echo(f"{utcnow():%H:%M:%S} {results}")
        db.session.remove()
        time_module.sleep(interval)


def register(app: Flask) -> None:
    app.cli.add_command(jobs_cli)


def secret_matches(header_value: str | None) -> bool:
    secret = current_app.config.get("JOBS_SECRET")
    return bool(secret) and header_value is not None and hmac.compare_digest(secret, header_value)
