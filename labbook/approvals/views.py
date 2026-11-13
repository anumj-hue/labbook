"""Approvals queue for Teachers and Admins (spec §4.4, §6.9)."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Booking, BookingStatus, EquipmentUnit, Role
from ..permissions import role_required, safe_next
from ..services import bookings as svc
from ..services.availability import type_timezone

bp = Blueprint("approvals", __name__, url_prefix="/approvals")


def pending_count() -> int:
    return db.session.scalar(
        select(func.count()).select_from(Booking).where(Booking.status == BookingStatus.PENDING)
    )


def _history(user_ids: set[int]) -> dict[int, dict[str, int]]:
    """Per-student booking history shown on each request (FR-P1)."""
    rows = db.session.execute(
        select(Booking.user_id, Booking.status, func.count(), func.sum(Booking.is_late_cancel))
        .where(Booking.user_id.in_(user_ids)).group_by(Booking.user_id, Booking.status)
    )
    history: dict[int, dict[str, int]] = {uid: {"total": 0, "completed": 0, "no_shows": 0, "late_cancels": 0}
                                          for uid in user_ids}
    for user_id, status, count, late in rows:
        h = history[user_id]
        h["total"] += count
        h["late_cancels"] += int(late or 0)
        if status is BookingStatus.COMPLETED:
            h["completed"] += count
        elif status is BookingStatus.NO_SHOW:
            h["no_shows"] += count
    return history


@bp.get("/")
@role_required(Role.TEACHER)
def index():
    requests_ = db.session.scalars(
        select(Booking).where(Booking.status == BookingStatus.PENDING).order_by(Booking.created_at)
        .options(selectinload(Booking.user), selectinload(Booking.type),
                 selectinload(Booking.unit).selectinload(EquipmentUnit.lab))
    ).all()
    history = _history({b.user_id for b in requests_ if b.user_id})
    return render_template("approvals/index.html", requests=requests_, history=history,
                           tz=type_timezone(requests_[0].type) if requests_ else None)


def _back():
    return redirect(safe_next(request.form.get("next")) or url_for("approvals.index"))


@bp.post("/<int:booking_id>/approve")
@role_required(Role.TEACHER)
def approve(booking_id: int):
    booking = db.get_or_404(Booking, booking_id)
    try:
        svc.approve(booking, current_user)
    except svc.BookingRefused as refused:
        db.session.rollback()
        flash(refused.violations[0].message, "error")
        return _back()
    db.session.commit()
    flash(f"Approved {booking.user.name if booking.user else 'the'} request for {booking.type.name}.", "success")
    return _back()


@bp.post("/<int:booking_id>/reject")
@role_required(Role.TEACHER)
def reject(booking_id: int):
    booking = db.get_or_404(Booking, booking_id)
    try:
        svc.reject(booking, current_user, request.form.get("reason", ""))
    except svc.BookingRefused as refused:
        db.session.rollback()
        flash(refused.violations[0].message, "error")
        return _back()
    db.session.commit()
    flash("Request rejected. The student has been told why.", "success")
    return _back()


@bp.post("/bulk-approve")
@role_required(Role.TEACHER)
def bulk_approve():
    ids = [int(i) for i in request.form.getlist("booking_id") if i.isdigit()]
    approved, problems = 0, []
    for booking in db.session.scalars(select(Booking).where(Booking.id.in_(ids))):
        try:
            with db.session.begin_nested():
                svc.approve(booking, current_user)
            approved += 1
        except svc.BookingRefused as refused:
            problems.append(f"#{booking.id}: {refused.violations[0].message}")
    db.session.commit()
    if approved:
        flash(f"Approved {approved} request{'s' if approved != 1 else ''}.", "success")
    for problem in problems:
        flash(problem, "error")
    if not ids:
        flash("Select at least one request.", "info")
    return redirect(url_for("approvals.index"))
