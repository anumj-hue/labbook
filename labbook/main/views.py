from dataclasses import dataclass
from datetime import datetime, time, timedelta

from flask import Blueprint, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from ..extensions import db
from ..models import (
    ACTIVE_STATUSES,
    AuditLog,
    Booking,
    BookingStatus,
    EquipmentType,
    EquipmentUnit,
    Lab,
    LogKind,
    MaintenanceLog,
    Role,
    Term,
    UnitStatus,
)
from ..services import bookings, desk, reports, settings
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
    glance = admin_week = None
    if current_user.has_role(Role.LAB_TECH):
        day = desk.desk_day(local_today())
        today_end = datetime.combine(local_today() + timedelta(days=1), time.min, default_tz())
        glance = {
            "due_out": len(day.due_out),
            "due_back": sum(1 for b in day.checked_out if b.ends_at < today_end),
            "overdue": len(day.overdue),
            "missing": len(day.missing),
            "units_out": db.session.scalar(select(func.count()).select_from(EquipmentUnit)
                                           .where(EquipmentUnit.status == UnitStatus.OUT_OF_SERVICE)),
            "issues": db.session.scalars(select(MaintenanceLog).where(MaintenanceLog.kind == LogKind.ISSUE,
                                                                      MaintenanceLog.resolved_at.is_(None))
                                         .order_by(MaintenanceLog.created_at.desc()).limit(5)).all(),
        }
    recent_audit = None
    if current_user.role is Role.ADMIN:
        admin_week = reports.week_stats(local_today())
        recent_audit = db.session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(5)).all()
    now = utcnow()
    upcoming = db.session.scalars(
        select(Booking).where(Booking.user_id == current_user.id, Booking.status.in_(ACTIVE_STATUSES),
                              Booking.ends_at > now).order_by(Booking.starts_at).limit(5)
    ).all()
    quota = bookings.quota_summary(current_user, now) if current_user.role is Role.STUDENT else None
    pending_mine = [b for b in upcoming if b.status is BookingStatus.PENDING]
    approvals = None
    if current_user.has_role(Role.TEACHER):
        approvals = {
            "count": db.session.scalar(select(func.count()).select_from(Booking)
                                       .where(Booking.status == BookingStatus.PENDING)),
            "items": db.session.scalars(select(Booking).where(Booking.status == BookingStatus.PENDING)
                                        .order_by(Booking.created_at).limit(5)).all(),
        }
    return render_template("main/home.html", checklist=checklist, recent_types=recent_types, glance=glance,
                           admin_week=admin_week, recent_audit=recent_audit,
                           today=local_today(), upcoming=upcoming, quota=quota, tz=default_tz(), now=now,
                           pending_mine=pending_mine, approvals=approvals)
