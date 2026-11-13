"""`flask create-admin` (FR-S1) and `flask seed` (FR-S2)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import click
from email_validator import EmailNotValidError, validate_email
from flask import Flask, current_app
from flask.cli import with_appcontext
from sqlalchemy import func, select

from .extensions import db
from .models import (
    Accessory,
    Booking,
    BookingStatus,
    EquipmentType,
    EquipmentUnit,
    Holiday,
    Lab,
    LogKind,
    MaintenanceBlock,
    MaintenanceLog,
    Role,
    Term,
    UnitStatus,
    User,
)
from .services import audit, privacy, settings, users
from .services.passwords import MIN_PASSWORD_LENGTH, hash_password
from .timeutil import local_today, utcnow


def register_cli(app: Flask) -> None:
    app.cli.add_command(create_admin)
    app.cli.add_command(seed)


def _prompt_password() -> str:
    while True:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        if len(password) >= MIN_PASSWORD_LENGTH:
            return password
        click.echo(f"Use at least {MIN_PASSWORD_LENGTH} characters.", err=True)


@click.command("create-admin")
@click.option("--email", required=True, help="Email address for the Admin account.")
@click.option("--name", required=True, help="Full name.")
@click.option("--invite", is_flag=True, help="Email a set-password link instead of prompting for a password.")
@click.option("--force", is_flag=True, help="Run even if an Admin already exists.")
@with_appcontext
def create_admin(email: str, name: str, invite: bool, force: bool) -> None:
    """Create the first Admin account (already verified)."""
    try:
        email = users.normalise_email(validate_email(email, check_deliverability=False).normalized)
    except EmailNotValidError as err:
        raise click.BadParameter(str(err), param_hint="--email") from None

    admin_exists = db.session.scalar(select(func.count()).select_from(User).where(User.role == Role.ADMIN)) > 0
    if admin_exists and not force:
        raise click.ClickException("An Admin already exists. Re-run with --force to create another.")
    if users.find_by_email(email):
        raise click.ClickException(f"A user with email {email} already exists. Promote them from Admin > Users.")

    password = None if invite else _prompt_password()
    privacy.current_notice()  # make sure version 1 of the notice exists
    user = User(
        name=name.strip(),
        email=email,
        role=Role.ADMIN,
        password_hash=hash_password(password) if password else None,
        email_verified_at=utcnow(),
    )
    db.session.add(user)
    db.session.flush()
    audit.record(None, "User", user.id, "create_admin", None, audit.snapshot(user, *users.USER_AUDIT_FIELDS))
    db.session.commit()

    if invite:
        if users.send_invite(user):
            click.echo(f"Admin {email} created. A set-password link has been emailed (valid 72 hours).")
        else:
            click.echo(f"Admin {email} created, but the invite email failed. Check the mail settings.", err=True)
    else:
        click.echo(f"Admin {email} created. You can sign in now.")


DEMO_PASSWORD = "labbook-demo"

_WEEKDAY_HOURS = {d: [["08:00", "18:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}

DEMO_LABS = [
    {"name": "Electronics Lab", "location": "Block A, Room 204",
     "term_hours": {**_WEEKDAY_HOURS, "sat": [["09:00", "13:00"]]},
     "vacation_hours": {d: [["09:00", "13:00"]] for d in ("mon", "tue", "wed", "thu", "fri")}},
    {"name": "Instrumentation Lab", "location": "Block B, Room 112",
     "term_hours": {d: [["09:00", "17:00"]] for d in ("mon", "tue", "wed", "thu", "fri")},
     "vacation_hours": {}},
]

# (name, category, lab index, unit prefix, unit count, requires_approval, purpose_required, max min, accessories)
DEMO_TYPES = [
    ("Digital Oscilloscope DSO-X 2002A", "Oscilloscopes", 0, "OSC", 4, False, False, None,
     [("×10 probe", 2), ("Power cord", 1)]),
    ("Function Generator 33500B", "Signal sources", 0, "FG", 3, False, False, None,
     [("BNC cable", 2), ("Power cord", 1)]),
    ("Triple-Output DC Power Supply E36313A", "Power supplies", 0, "PSU", 5, False, False, None,
     [("Banana lead set", 1), ("Power cord", 1)]),
    ("Digital Multimeter 34461A", "Meters", 0, "DMM", 6, False, False, 240, [("Test lead set", 1)]),
    ("Soldering Station FX-951", "Tools", 0, "SOL", 4, False, False, None,
     [("Tip cleaner", 1), ("Iron holder", 1)]),
    ("Spectrum Analyzer N9320B", "RF", 1, "SA", 1, True, True, 120,
     [("N-to-BNC adapter", 1), ("Power cord", 1)]),
    ("Thermal Camera FLIR E8", "Imaging", 1, "TC", 2, True, False, 120, [("Battery", 2), ("USB cable", 1)]),
    ("Logic Analyzer 16-ch", "Digital", 1, "LA", 2, False, False, None, [("Probe harness", 1), ("USB cable", 1)]),
]

DEMO_USERS = [
    ("Admin User", "admin@example.edu", Role.ADMIN),
    ("Dr. Teacher Demo", "teacher@example.edu", Role.TEACHER),
    ("Lab Tech Demo", "labtech@example.edu", Role.LAB_TECH),
    ("Student One", "student1@example.edu", Role.STUDENT),
    ("Student Two", "student2@example.edu", Role.STUDENT),
]


# (user email, type unit prefix, unit number, working-day offset, start, end, status)
DEMO_BOOKINGS = [
    ("student1@example.edu", "OSC", 1, 1, "10:00", "12:00", BookingStatus.CONFIRMED),
    ("student1@example.edu", "DMM", 2, 2, "14:00", "15:00", BookingStatus.CONFIRMED),
    ("student2@example.edu", "OSC", 2, 1, "10:00", "11:30", BookingStatus.CONFIRMED),
    ("student2@example.edu", "FG", 1, 1, "13:00", "15:00", BookingStatus.CONFIRMED),
    ("labtech@example.edu", "OSC", 3, 1, "15:00", "17:00", BookingStatus.CONFIRMED),
    ("teacher@example.edu", "SA", 1, 2, "09:00", "11:00", BookingStatus.CONFIRMED),
    ("teacher@example.edu", "OSC", 4, 3, "09:00", "12:00", BookingStatus.CONFIRMED),
    ("student1@example.edu", "PSU", 1, -1, "10:00", "12:00", BookingStatus.COMPLETED),
    ("student2@example.edu", "TC", 1, 2, "14:00", "16:00", BookingStatus.PENDING),
]


def _working_day(today: date, offset: int, holidays: set[date]) -> date:
    """The offset-th Mon–Fri day from today (negative = past), skipping seeded holidays."""
    step = 1 if offset >= 0 else -1
    day, remaining = today, abs(offset)
    while remaining:
        day += timedelta(days=step)
        if day.weekday() < 5 and day not in holidays:
            remaining -= 1
    return day


def _seed_bookings(people: dict[str, User], today: date) -> None:
    tz = ZoneInfo(current_app.config["DEFAULT_TIMEZONE"])
    holidays = {h.start_date for h in db.session.scalars(select(Holiday))}
    units = {u.asset_tag: u for u in db.session.scalars(select(EquipmentUnit))}
    for email, prefix, number, offset, start, end, status in DEMO_BOOKINGS:
        user, unit = people[email], units[f"{prefix}-{number:02d}"]
        day = _working_day(today, offset, holidays)
        db.session.add(Booking(
            unit_id=unit.id, type_id=unit.type_id, user_id=user.id, created_by_id=user.id,
            starts_at=datetime.combine(day, time.fromisoformat(start), tz),
            ends_at=datetime.combine(day, time.fromisoformat(end), tz),
            status=status, user_role_at_booking=user.role, purpose="Demo booking",
        ))


def _seed_desk(people: dict[str, User], today: date) -> None:
    """Things for the Today desk and Units & Maintenance pages, relative to now."""
    now = utcnow().replace(second=0, microsecond=0)
    now -= timedelta(minutes=now.minute % 15)
    units = {u.asset_tag: u for u in db.session.scalars(select(EquipmentUnit))}

    def booking(email, tag, start, end, status):
        user, unit = people[email], units[tag]
        db.session.add(Booking(unit_id=unit.id, type_id=unit.type_id, user_id=user.id, created_by_id=user.id,
                               starts_at=start, ends_at=end, status=status, user_role_at_booking=user.role,
                               purpose="Demo booking"))

    booking("student2@example.edu", "OSC-04", now - timedelta(hours=3), now - timedelta(hours=1),
            BookingStatus.CHECKED_OUT)  # overdue
    booking("student1@example.edu", "DMM-01", now - timedelta(minutes=30), now + timedelta(minutes=90),
            BookingStatus.CHECKED_OUT)
    booking("teacher@example.edu", "PSU-02", now + timedelta(minutes=30), now + timedelta(minutes=90),
            BookingStatus.CONFIRMED)

    tech = people["labtech@example.edu"]
    db.session.add_all([
        MaintenanceLog(unit_id=units["DMM-06"].id, kind=LogKind.OUT_OF_SERVICE, notes="Display flickers.",
                       reported_by_id=tech.id),
        MaintenanceLog(unit_id=units["SOL-02"].id, kind=LogKind.ISSUE, notes="Tip worn, needs replacing.",
                       reported_by_id=people["student1@example.edu"].id),
    ])
    holidays = {h.start_date for h in db.session.scalars(select(Holiday))}
    day = _working_day(today, 3, holidays)
    tz = ZoneInfo(current_app.config["DEFAULT_TIMEZONE"])
    db.session.add(MaintenanceBlock(unit_id=units["FG-03"].id, reason="Annual calibration", created_by_id=tech.id,
                                    starts_at=datetime.combine(day, time(9), tz),
                                    ends_at=datetime.combine(day, time(12), tz)))


def _clear_all_data() -> None:
    for table in reversed(db.metadata.sorted_tables):
        db.session.execute(table.delete())


@click.command("seed")
@click.option("--reset", is_flag=True, help="Delete all existing data first.")
@with_appcontext
def seed(reset: bool) -> None:
    """Load demo labs, equipment and users (development and staging only)."""
    if current_app.config["ENV_NAME"] == "production":
        raise click.ClickException("Refusing to seed a production database.")
    has_data = db.session.scalar(select(func.count()).select_from(User)) > 0
    if has_data and not reset:
        raise click.ClickException("The database already has data. Re-run with --reset to wipe it first.")
    if reset:
        _clear_all_data()

    notice = privacy.current_notice()
    today = local_today()
    now = utcnow()

    labs = [Lab(**spec, timezone=current_app.config["DEFAULT_TIMEZONE"]) for spec in DEMO_LABS]
    db.session.add_all(labs)
    db.session.add(Term(name=f"Current Semester {today.year}", start_date=today - timedelta(days=45),
                        end_date=today + timedelta(days=75)))
    db.session.add(Holiday(start_date=today + timedelta(days=10), end_date=today + timedelta(days=10),
                           reason="Department Day"))
    db.session.flush()

    for name, category, lab_idx, prefix, count, approval, purpose, max_min, accessories in DEMO_TYPES:
        eq_type = EquipmentType(
            name=name, category=category, requires_approval=approval, purpose_required=purpose,
            max_duration_min=max_min,
            description=f"**{name}** for coursework and project use.\n\nAsk a lab tech if you need a demo.",
            safety_notes="Switch off and unplug after use. Report any damage at the lab desk." if approval else "",
        )
        db.session.add(eq_type)
        db.session.flush()
        for order, (acc_name, qty) in enumerate(accessories):
            db.session.add(Accessory(type_id=eq_type.id, name=acc_name, expected_qty=qty, sort_order=order))
        for n in range(1, count + 1):
            status = UnitStatus.OUT_OF_SERVICE if prefix == "DMM" and n == count else UnitStatus.AVAILABLE
            db.session.add(EquipmentUnit(type_id=eq_type.id, lab_id=labs[lab_idx].id, asset_tag=f"{prefix}-{n:02d}",
                                         serial_no=f"SN{eq_type.id:02d}{n:04d}", status=status))

    password_hash = hash_password(DEMO_PASSWORD)
    people = {}
    for name, email, role in DEMO_USERS:
        people[email] = User(name=name, email=email, role=role, password_hash=password_hash,
                             email_verified_at=now, consent_at=now, consent_version=notice.version)
        db.session.add(people[email])
    db.session.flush()
    _seed_bookings(people, today)
    _seed_desk(people, today)

    settings.set_value("allowed_email_domains", [], None)
    settings.mark_rules_reviewed(None)
    db.session.commit()

    click.echo("Seeded demo data. Sign in with any of these (password: labbook-demo):")
    for _name, email, role in DEMO_USERS:
        click.echo(f"  {role.label:<9} {email}")
