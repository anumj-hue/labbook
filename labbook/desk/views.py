"""Today desk for Lab Techs (spec §4.8, §6.9)."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Booking, Condition, Role
from ..permissions import role_required, safe_next
from ..services import desk
from ..services.bookings import BookingRefused
from ..timeutil import default_tz, local_today, utcnow

bp = Blueprint("desk", __name__, url_prefix="/today")


@bp.get("/")
@role_required(Role.LAB_TECH)
def index():
    day = desk.desk_day(local_today())
    return render_template("desk/index.html", day=day, now=utcnow(), tz=default_tz(), today=local_today())


def _condition() -> Condition | None:
    value = request.form.get("condition", "")
    return Condition(value) if value in Condition.__members__ else None


def _quantities(prefix: str) -> dict[int, int]:
    """Ticked accessories and their quantities from the checklist (unticked = 0)."""
    result = {}
    for key, value in request.form.items():
        if key.startswith(f"{prefix}_qty_"):
            accessory_id = int(key.rsplit("_", 1)[1])
            ticked = "1" in request.form.getlist(f"{prefix}_tick_{accessory_id}")
            try:
                result[accessory_id] = max(0, int(value)) if ticked else 0
            except ValueError:
                result[accessory_id] = 0
    return result


def _booking(booking_id: int) -> Booking:
    return db.get_or_404(Booking, booking_id)


@bp.route("/<int:booking_id>/check-out", methods=["GET", "POST"])
@role_required(Role.LAB_TECH)
def check_out(booking_id: int):
    booking = _booking(booking_id)
    error = None
    if request.method == "POST":
        condition = _condition()
        if condition is None:
            error = "Choose the unit's condition."
        else:
            try:
                desk.check_out(booking, current_user, condition, request.form.get("notes", ""), _quantities("out"))
            except BookingRefused as refused:
                db.session.rollback()
                error = refused.violations[0].message
            else:
                db.session.commit()
                flash(f"{booking.unit.asset_tag} checked out to {booking.user.name if booking.user else 'the booker'}.",
                      "success")
                return redirect(url_for("desk.index"))
    items = [(a, a.expected_qty) for a in booking.type.active_accessories]
    return render_template("desk/check.html", booking=booking, kind="out", items=items, error=error,
                           tz=default_tz()), \
        (400 if error else 200)


@bp.route("/<int:booking_id>/check-in", methods=["GET", "POST"])
@role_required(Role.LAB_TECH)
def check_in(booking_id: int):
    booking = _booking(booking_id)
    error = None
    if request.method == "POST":
        condition = _condition()
        if condition is None:
            error = "Choose the unit's condition."
        else:
            take_out = condition is Condition.DAMAGED and request.form.get("out_of_service") == "1"
            try:
                _, missing = desk.check_in(booking, current_user, condition, request.form.get("notes", ""),
                                           _quantities("in"), take_out_of_service=take_out)
            except BookingRefused as refused:
                db.session.rollback()
                error = refused.violations[0].message
            else:
                db.session.commit()
                message = f"{booking.unit.asset_tag} checked in."
                if missing:
                    message += f" Missing: {', '.join(missing)}. The booker and lab techs have been told."
                if take_out:
                    message += f" {booking.unit.asset_tag} is now out of service."
                flash(message, "success" if not missing else "info")
                return redirect(url_for("desk.index"))
    items = [(item.accessory, item.qty) for item in desk.outgoing_items(booking) if item.qty]
    return render_template("desk/check.html", booking=booking, kind="in", items=items, error=error,
                           tz=default_tz()), \
        (400 if error else 200)


@bp.post("/<int:booking_id>/<action>")
@role_required(Role.LAB_TECH)
def action(booking_id: int, action: str):
    booking = _booking(booking_id)
    try:
        if action == "no-show":
            desk.mark_no_show(booking, current_user)
            message = "Marked as a no-show."
        elif action == "reverse-no-show":
            desk.reverse_no_show(booking, current_user)
            message = "No-show reversed."
        elif action == "resolve-missing":
            if not booking.accessories_missing:
                abort(400)
            desk.resolve_missing(booking, current_user)
            message = "Missing accessories resolved."
        else:
            abort(404)
    except BookingRefused as refused:
        db.session.rollback()
        flash(refused.violations[0].message, "error")
    else:
        db.session.commit()
        flash(message, "success")
    return redirect(safe_next(request.form.get("next")) or url_for("desk.index"))

