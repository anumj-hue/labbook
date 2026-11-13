from dataclasses import dataclass

from flask import Blueprint, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from ..extensions import db
from ..models import ACTIVE_STATUSES, Booking, EquipmentType, EquipmentUnit, Lab, Role, Term, UnitStatus, User
from ..services import bookings, settings
from ..timeutil import default_tz, local_today, utcnow

bp = Blueprint("main", __name__)


@dataclass(frozen=True)
class ChecklistStep:
    label: str
    done: bool
    url: str


def setup_checklist() -> list[ChecklistStep]:
    """FR-S3: first-run steps shown to Admins on Home until all are done."""
    labs = db.session.scalars(select(Lab)).all()
    has = lambda model: db.session.scalar(select(func.count()).select_from(model)) > 0  # noqa: E731
    return [
        ChecklistStep("Add a lab and its opening hours", any(lab.has_term_hours for lab in labs),
                      url_for("admin.labs")),
        ChecklistStep("Add a term", has(Term), url_for("admin.calendar")),
        ChecklistStep("Add equipment types and units", has(EquipmentType) and has(EquipmentUnit),
                      url_for("admin.types")),
        ChecklistStep("Set the allowed email domains", settings.is_set("allowed_email_domains"),
                      url_for("admin.settings")),
        ChecklistStep("Review the booking rules", settings.get("rules_reviewed_at") is not None,
                      url_for("admin.settings")),
    ]


@bp.get("/")
@login_required
def home():
    checklist = setup_checklist() if current_user.role is Role.ADMIN else None
    if checklist and all(step.done for step in checklist):
        checklist = None
    recent_types = db.session.scalars(
        select(EquipmentType).where(EquipmentType.is_archived.is_(False)).order_by(EquipmentType.name).limit(6)
    ).all()
    stats = None
    if current_user.has_role(Role.LAB_TECH):
        count = lambda stmt: db.session.scalar(stmt)  # noqa: E731
        stats = {
            "units_in_service": count(select(func.count()).select_from(EquipmentUnit)
                                      .where(EquipmentUnit.status == UnitStatus.AVAILABLE)),
            "units_out": count(select(func.count()).select_from(EquipmentUnit)
                               .where(EquipmentUnit.status == UnitStatus.OUT_OF_SERVICE)),
            "types": count(select(func.count()).select_from(EquipmentType)
                           .where(EquipmentType.is_archived.is_(False))),
        }
        if current_user.role is Role.ADMIN:
            stats["users"] = count(select(func.count()).select_from(User).where(User.active.is_(True)))
    now = utcnow()
    upcoming = db.session.scalars(
        select(Booking).where(Booking.user_id == current_user.id, Booking.status.in_(ACTIVE_STATUSES),
                              Booking.ends_at > now).order_by(Booking.starts_at).limit(5)
    ).all()
    quota = bookings.quota_summary(current_user, now) if current_user.role is Role.STUDENT else None
    return render_template("main/home.html", checklist=checklist, recent_types=recent_types, stats=stats,
                           today=local_today(), upcoming=upcoming, quota=quota, tz=default_tz(), now=now)
