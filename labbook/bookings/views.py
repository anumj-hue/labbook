from datetime import date, datetime, time, timedelta

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    ACTIVE_STATUSES,
    Booking,
    BookingStatus,
    EquipmentType,
    EquipmentUnit,
    Lab,
    Role,
    UnitStatus,
)
from ..rules import format_minutes
from ..services import bookings as svc
from ..services import settings, timeline
from ..services.availability import in_service_units, type_timezone
from ..timeutil import default_tz, fmt_date, fmt_datetime, fmt_time, utcnow
from .forms import BookingForm, CancelForm

bp = Blueprint("bookings", __name__)

PER_PAGE = 25
CALENDAR_STATUSES = (*ACTIVE_STATUSES, BookingStatus.COMPLETED, BookingStatus.NO_SHOW)


# --- Shared picker --------------------------------------------------------------------------------

def _parse_date(value: str | None, fallback: date) -> date:
    try:
        return date.fromisoformat(value) if value else fallback
    except ValueError:
        return fallback


def _unit_choices(eq_type: EquipmentType) -> list[tuple[int, str]]:
    return [(0, "Any available unit")] + [(u.id, f"{u.asset_tag} · {u.lab.name}") for u in in_service_units(eq_type)]


def picker_form(eq_type: EquipmentType, booking: Booking | None = None) -> BookingForm:
    """Booking form pre-filled from the query string (or the booking being edited)."""
    tz = type_timezone(eq_type)
    if request.method == "POST":
        form = BookingForm()
    elif booking is not None:
        start, end = booking.starts_at.astimezone(tz), booking.ends_at.astimezone(tz)
        form = BookingForm(data={
            "date": _parse_date(request.args.get("date"), start.date()), "start": f"{start:%H:%M}",
            "end": f"{end:%H:%M}", "unit_id": booking.unit_id, "purpose": booking.purpose,
            "booking_id": booking.id,
        })
    else:
        today = utcnow().astimezone(tz).date()
        form = BookingForm(data={
            "date": _parse_date(request.args.get("date"), today),
            "start": request.args.get("start", ""), "end": request.args.get("end", ""),
            "unit_id": request.args.get("unit", 0, type=int),
        })
    form.type_id.data = str(eq_type.id)
    form.unit_id.choices = _unit_choices(eq_type)
    return form


def picker_context(eq_type: EquipmentType, form: BookingForm, booking: Booking | None = None,
                   violations=None, override=None) -> dict:
    day = form.date.data or utcnow().astimezone(type_timezone(eq_type)).date()
    tl = timeline.build(eq_type, day, current_user, editing_booking_id=booking.id if booking else None)
    rules = settings.effective_rules(eq_type)
    max_minutes = rules["max_duration_min"]
    params = {"start": form.start.data or None, "end": form.end.data or None, "unit": form.unit_id.data or None}
    if booking is not None:
        nav = url_for("bookings.edit", booking_id=booking.id, date="__DATE__")
    else:
        nav = url_for("catalogue.detail", type_id=eq_type.id, date="__DATE__", **params)
    selected = next((label for value, label in form.unit_id.choices if value and value == form.unit_id.data), None)
    return {
        "eq_type": eq_type,
        "form": form,
        "nav_url_template": nav,
        "selected_unit_label": selected or "Any available unit",
        "booking": booking,
        "timeline": tl,
        "rules": rules,
        "max_duration_label": format_minutes(max_minutes),
        "max_duration_min": max_minutes,
        "violations": violations or [],
        "override": override,
        "book_label": "Request booking" if svc.needs_approval(current_user, eq_type) else "Book",
        "day": day,
        "prev_day": day - timedelta(days=1),
        "next_day": day + timedelta(days=1),
        "is_today": day == utcnow().astimezone(tl.tz).date(),
    }


def render_type_page(eq_type: EquipmentType, form: BookingForm, violations=None, status: int = 200,
                     override=None):
    """The equipment detail page (spec FR-E3), which hosts the booking picker (spec §6.7)."""
    units = [u for u in eq_type.units if u.status is not UnitStatus.RETIRED]
    return render_template("catalogue/detail.html", units=units,
                           **picker_context(eq_type, form, violations=violations, override=override)), status


def override_payload(option) -> dict | None:
    """What the picker shows a teacher before bumping a student booking (spec §4.7, §6.7)."""
    if option is None:
        return None
    victim = option.booking
    return {"unit": option.unit.asset_tag, "student": victim.user.name if victim.user else "a student",
            "when": f"{fmt_time(victim.starts_at)}–{fmt_time(victim.ends_at)}",
            "made": fmt_datetime(victim.created_at)}


def _request_from_form(form: BookingForm, eq_type: EquipmentType, booking: Booking | None = None):
    times = form.times(type_timezone(eq_type))
    if times is None:
        return None
    user = booking.user if booking else current_user
    return svc.BookingRequest(user=user, eq_type=eq_type, starts_at=times[0], ends_at=times[1],
                              unit_id=form.unit_id.data or None, purpose=form.purpose.data or "", booking=booking)


def _summary(form: BookingForm, eq_type: EquipmentType) -> dict:
    times = form.times(type_timezone(eq_type))
    unit = next((label for value, label in form.unit_id.choices if value == form.unit_id.data and value), None)
    if times is None:
        return {"unit": unit or "Any available unit", "when": "", "duration": ""}
    start, end = times
    return {
        "unit": unit or "Any available unit",
        "when": f"{fmt_date(start.date())} · {start:%H:%M}–{end:%H:%M}",
        "duration": format_minutes(int((end - start).total_seconds() // 60)) if end > start else "",
    }


def _own_or_staff(booking: Booking) -> None:
    if booking.user_id != current_user.id and not current_user.has_role(Role.TEACHER, Role.LAB_TECH):
        abort(404)  # students never learn about other people's bookings


# --- Create / validate ----------------------------------------------------------------------------

@bp.post("/bookings/new")
@login_required
def create():
    eq_type = db.get_or_404(EquipmentType, request.form.get("type_id", type=int))
    form = picker_form(eq_type)
    violations, override = [], None
    if form.validate_on_submit():
        req = _request_from_form(form, eq_type)
        override_reason = request.form.get("override_reason", "") if request.form.get("override") == "1" else None
        try:
            booking = svc.create(req, current_user, override_reason=override_reason)
        except svc.BookingRefused as refused:
            db.session.rollback()
            violations = refused.violations
            override = override_payload(svc.evaluate(req).override)
            db.session.rollback()
        else:
            db.session.commit()
            when = f"{fmt_date(booking.starts_at)}, {booking.starts_at.astimezone(type_timezone(eq_type)):%H:%M}"
            if booking.status is BookingStatus.PENDING:
                flash(f"Request sent for {when}. {booking.unit.asset_tag} is held while a teacher reviews it.",
                      "success")
            elif override_reason is not None:
                flash(f"Booked {booking.unit.asset_tag} for {when}. The student's booking was overridden and "
                      f"they've been told why.", "success")
            else:
                flash(f"Booked {booking.unit.asset_tag} for {when}.", "success")
            return redirect(url_for("bookings.detail", booking_id=booking.id, new=1))
    return render_type_page(eq_type, form, violations, 400, override=override)


@bp.post("/bookings/validate")
@login_required
def validate():
    """Live validation for the picker: the same rules as submit, without writing (spec §6.7)."""
    eq_type = db.get_or_404(EquipmentType, request.form.get("type_id", type=int))
    booking = None
    if request.form.get("booking_id", type=int):
        booking = db.get_or_404(Booking, request.form.get("booking_id", type=int))
        if booking.user_id != current_user.id:
            abort(404)
    form = picker_form(eq_type, booking)
    form.validate()
    summary = _summary(form, eq_type)
    req = _request_from_form(form, eq_type, booking)
    if req is None:
        return jsonify(ok=False, incomplete=True, violations=[], summary=summary)
    result = svc.evaluate(req)
    db.session.rollback()
    if result.ok and not form.unit_id.data and result.free:
        summary["unit"] = f"Any available unit (e.g. {result.free[0].asset_tag})"
    override = override_payload(result.override)
    return jsonify(ok=result.ok, incomplete=False, summary=summary, override=override,
                   violations=[{"code": v.code, "message": v.message} for v in result.violations])


# --- Detail / edit / cancel -----------------------------------------------------------------------

@bp.get("/bookings/<int:booking_id>")
@login_required
def detail(booking_id: int):
    booking = db.get_or_404(Booking, booking_id)
    _own_or_staff(booking)
    tz = type_timezone(booking.type)
    is_owner = booking.user_id == current_user.id
    staff_can_cancel = (not is_owner and current_user.has_role(Role.LAB_TECH) and not booking.has_started
                        and booking.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED))
    return render_template("bookings/detail.html", booking=booking, tz=tz, cancel_form=CancelForm(),
                           is_owner=is_owner, staff_can_cancel=staff_can_cancel,
                           can_decide=booking.status is BookingStatus.PENDING and current_user.has_role(Role.TEACHER),
                           just_created=request.args.get("new") == "1")


@bp.route("/bookings/<int:booking_id>/edit", methods=["GET", "POST"])
@login_required
def edit(booking_id: int):
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(404)
    if not booking.can_change:
        flash("This booking can no longer be changed.", "error")
        return redirect(url_for("bookings.detail", booking_id=booking.id))
    eq_type = booking.type
    form = picker_form(eq_type, booking)
    violations = []
    if form.validate_on_submit():
        req = _request_from_form(form, eq_type, booking)
        try:
            svc.reschedule(booking, req.starts_at, req.ends_at, req.unit_id, req.purpose, current_user)
        except svc.BookingRefused as refused:
            db.session.rollback()
            violations = refused.violations
        else:
            db.session.commit()
            flash("Booking updated.", "success")
            return redirect(url_for("bookings.detail", booking_id=booking.id))
    status = 400 if request.method == "POST" else 200
    return render_template("bookings/edit.html", **picker_context(eq_type, form, booking, violations)), status


@bp.post("/bookings/<int:booking_id>/cancel")
@login_required
def cancel(booking_id: int):
    """Owners cancel their own bookings (FR-C1); Lab Techs and Admins can cancel any, with a reason (FR-C4)."""
    booking = db.get_or_404(Booking, booking_id)
    is_owner = booking.user_id == current_user.id
    if not is_owner and not current_user.has_role(Role.LAB_TECH):
        abort(404)
    form = CancelForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        svc.cancel(booking, current_user, form.reason.data or None)
    except svc.BookingRefused as refused:
        db.session.rollback()
        flash(refused.violations[0].message, "error")
        return redirect(url_for("bookings.detail", booking_id=booking.id))
    db.session.commit()
    if not is_owner:
        flash("Booking cancelled. The booker has been told why.", "success")
        return redirect(url_for("bookings.detail", booking_id=booking.id))
    note = " It counts as a late cancellation." if booking.is_late_cancel else ""
    flash(f"Booking cancelled.{note}", "success")
    return redirect(url_for("bookings.index", undo=booking.id))


@bp.post("/bookings/<int:booking_id>/undo-cancel")
@login_required
def undo_cancel(booking_id: int):
    booking = db.get_or_404(Booking, booking_id)
    if booking.user_id != current_user.id:
        abort(404)
    try:
        svc.undo_cancel(booking, current_user)
    except svc.BookingRefused as refused:
        db.session.rollback()
        flash(refused.violations[0].message, "error")
        return redirect(url_for("bookings.index"))
    db.session.commit()
    flash("Cancellation undone. Your booking is back.", "success")
    return redirect(url_for("bookings.detail", booking_id=booking.id))


# --- My Bookings ----------------------------------------------------------------------------------

@bp.get("/bookings")
@login_required
def index():
    tab = request.args.get("tab", "upcoming")
    now = utcnow()
    stmt = select(Booking).where(Booking.user_id == current_user.id).options(
        selectinload(Booking.unit).selectinload(EquipmentUnit.lab), selectinload(Booking.type))
    if tab == "pending":
        stmt = stmt.where(Booking.status == BookingStatus.PENDING).order_by(Booking.starts_at)
    elif tab == "past":
        stmt = stmt.where(or_(Booking.ends_at <= now, Booking.status.not_in(ACTIVE_STATUSES))) \
                   .order_by(Booking.starts_at.desc())
    else:
        tab = "upcoming"
        stmt = stmt.where(Booking.status.in_(ACTIVE_STATUSES), Booking.ends_at > now).order_by(Booking.starts_at)
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)

    undo_booking = None
    undo_id = request.args.get("undo", type=int)
    if undo_id:
        candidate = db.session.get(Booking, undo_id)
        if candidate is not None and svc.can_undo_cancel(candidate, current_user):
            undo_booking = candidate
    return render_template("bookings/index.html", page=page, tab=tab, undo_booking=undo_booking,
                           tz=default_tz())


# --- Calendar -------------------------------------------------------------------------------------

def _lanes(items: list[Booking]) -> dict[int, tuple[int, int]]:
    """Side-by-side lanes for overlapping blocks in one day column: booking id -> (lane, lane count)."""
    result: dict[int, tuple[int, int]] = {}
    cluster: list[Booking] = []
    lane_ends: list[datetime] = []
    cluster_end = None

    def close_cluster():
        for b in cluster:
            result[b.id] = (result[b.id][0], len(lane_ends))

    for b in sorted(items, key=lambda x: (x.starts_at, x.ends_at)):
        if cluster_end is not None and b.starts_at >= cluster_end:
            close_cluster()
            cluster, lane_ends = [], []
        lane = next((i for i, end in enumerate(lane_ends) if end <= b.starts_at), None)
        if lane is None:
            lane_ends.append(b.ends_at)
            lane = len(lane_ends) - 1
        else:
            lane_ends[lane] = b.ends_at
        result[b.id] = (lane, 0)
        cluster.append(b)
        cluster_end = b.ends_at if cluster_end is None else max(cluster_end, b.ends_at)
    close_cluster()
    return result


@bp.get("/calendar")
@login_required
def calendar():
    tz = default_tz()
    today = utcnow().astimezone(tz).date()
    view = "day" if request.args.get("view") == "day" else "week"
    day = _parse_date(request.args.get("date"), today)
    lab_id = request.args.get("lab", type=int)
    type_id = request.args.get("type", type=int)
    days = [day] if view == "day" else [day - timedelta(days=day.weekday()) + timedelta(days=i) for i in range(7)]
    range_start = datetime.combine(days[0], time.min, tz)
    range_end = datetime.combine(days[-1] + timedelta(days=1), time.min, tz)

    stmt = (
        select(Booking)
        .join(Booking.unit)
        .where(Booking.status.in_(CALENDAR_STATUSES), Booking.starts_at < range_end, Booking.ends_at > range_start)
        .options(selectinload(Booking.unit).selectinload(EquipmentUnit.lab), selectinload(Booking.type),
                 selectinload(Booking.user))
        .order_by(Booking.starts_at)
    )
    if lab_id:
        stmt = stmt.where(EquipmentUnit.lab_id == lab_id)
    if type_id:
        stmt = stmt.where(Booking.type_id == type_id)
    items = db.session.scalars(stmt).all()

    by_day: dict[date, list[Booking]] = {d: [] for d in days}
    for b in items:
        local_day = b.starts_at.astimezone(tz).date()
        if local_day in by_day:
            by_day[local_day].append(b)
    lanes = {d: _lanes(bs) for d, bs in by_day.items()}

    minutes = [8 * 60, 18 * 60]  # default visible range
    for b in items:
        s, e = b.starts_at.astimezone(tz), b.ends_at.astimezone(tz)
        minutes += [s.hour * 60 + s.minute, e.hour * 60 + e.minute if e.date() == s.date() else 24 * 60]
    first_hour, last_hour = min(minutes) // 60, -(-max(minutes) // 60)

    step = timedelta(days=1 if view == "day" else 7)
    return render_template(
        "bookings/calendar.html", view=view, day=day, days=days, by_day=by_day, lanes=lanes, tz=tz, today=today,
        first_hour=first_hour, last_hour=last_hour, hours=last_hour - first_hour, px_per_hour=56,
        lab_id=lab_id, type_id=type_id,
        labs=db.session.scalars(select(Lab).order_by(Lab.name)).all(),
        types=db.session.scalars(select(EquipmentType).where(EquipmentType.is_archived.is_(False))
                                 .order_by(EquipmentType.name)).all(),
        prev_day=day - step, next_day=day + step,
        can_see_names=current_user.has_role(Role.TEACHER, Role.LAB_TECH),
    )

