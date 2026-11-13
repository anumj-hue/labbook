"""Global rule defaults (spec §4.6) with per-equipment-type overrides."""

from dataclasses import dataclass

from ..extensions import db
from ..models import EquipmentType, Setting, User
from ..timeutil import utcnow
from . import audit


@dataclass(frozen=True)
class RuleSpec:
    key: str
    label: str
    unit: str
    minimum: int
    maximum: int
    group: str
    step: int = 1


RULES: tuple[RuleSpec, ...] = (
    RuleSpec("max_duration_min", "Max duration per booking", "minutes", 15, 1440, "Bookings", 15),
    RuleSpec("student_advance_days", "Advance booking window: Students", "days", 0, 365, "Bookings"),
    RuleSpec("staff_advance_days", "Advance booking window: Teachers, Lab Techs, Admins", "days", 0, 730,
             "Bookings"),
    RuleSpec("late_cancel_minutes", "Late cancellation threshold", "minutes", 0, 1440, "Bookings"),
    RuleSpec("student_max_active", "Max active (upcoming) bookings", "bookings", 0, 100, "Student quotas"),
    RuleSpec("student_max_weekly_hours", "Max booked hours per ISO week", "hours", 0, 168, "Student quotas"),
    RuleSpec("pending_expiry_hours", "Pending request expiry", "hours", 1, 720, "Approvals & overrides"),
    RuleSpec("override_min_notice_hours", "Minimum notice for teacher override", "hours", 0, 720,
             "Approvals & overrides"),
    RuleSpec("grace_minutes", "Check-out grace period (no-show threshold)", "minutes", 0, 240, "No-shows"),
    RuleSpec("no_show_limit", "No-shows before suspension", "no-shows", 1, 50, "No-shows"),
    RuleSpec("no_show_window_days", "No-show counting window", "days", 1, 365, "No-shows"),
    RuleSpec("suspension_days", "Suspension length", "days", 1, 365, "No-shows"),
    RuleSpec("waitlist_max_entries", "Max waitlist entries per student", "entries", 0, 50,
             "Waitlist & recurring"),
    RuleSpec("waitlist_claim_minutes", "Waitlist claim window", "minutes", 5, 1440, "Waitlist & recurring"),
    RuleSpec("recurring_max_weeks", "Max recurring series length (no term set)", "weeks", 1, 52,
             "Waitlist & recurring"),
    RuleSpec("retention_years", "Data retention before anonymisation", "years", 1, 20, "Data retention"),
)

DEFAULTS: dict[str, object] = {
    "max_duration_min": 180,
    "student_advance_days": 14,
    "staff_advance_days": 60,
    "late_cancel_minutes": 60,
    "student_max_active": 3,
    "student_max_weekly_hours": 8,
    "pending_expiry_hours": 48,
    "override_min_notice_hours": 24,
    "grace_minutes": 15,
    "no_show_limit": 3,
    "no_show_window_days": 30,
    "suspension_days": 7,
    "waitlist_max_entries": 3,
    "waitlist_claim_minutes": 30,
    "recurring_max_weeks": 16,
    "retention_years": 2,
    "allowed_email_domains": [],
    "rules_reviewed_at": None,
}


def get(key: str):
    row = db.session.get(Setting, key)
    return row.value if row is not None else DEFAULTS[key]


def is_set(key: str) -> bool:
    return db.session.get(Setting, key) is not None


def set_value(key: str, value, actor: User | None) -> bool:
    """Stores a setting and audits the change. Returns True if the value changed."""
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.session.get(Setting, key)
    before = row.value if row is not None else None
    if row is not None and before == value:
        return False
    if row is None:
        db.session.add(Setting(key=key, value=value))
    else:
        row.value = value
    audit.record(actor, "Setting", key, "update", {"value": before}, {"value": value})
    return True


def mark_rules_reviewed(actor: User | None) -> None:
    set_value("rules_reviewed_at", utcnow().isoformat(), actor)


def effective_rules(eq_type: EquipmentType) -> dict[str, object]:
    """Rule values that apply to one equipment type (type values override global defaults)."""
    return {
        "max_duration_min": eq_type.max_duration_min or get("max_duration_min"),
        "student_advance_days": (
            eq_type.student_advance_days
            if eq_type.student_advance_days is not None
            else get("student_advance_days")
        ),
        "staff_advance_days": get("staff_advance_days"),
        "requires_approval": eq_type.requires_approval,
        "purpose_required": eq_type.purpose_required,
    }


def normalise_domain(value: str) -> str:
    return value.strip().lower().lstrip("@")
