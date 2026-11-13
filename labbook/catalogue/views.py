from datetime import date, datetime, time

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from ..bookings.views import picker_form, render_type_page
from ..extensions import db
from ..models import EquipmentType, EquipmentUnit, Lab, Role, UnitStatus
from ..services.availability import free_units, now_status
from ..services.calendar import LabDayCache
from ..timeutil import default_tz, utcnow

bp = Blueprint("catalogue", __name__, url_prefix="/equipment")


@bp.get("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    lab_id = request.args.get("lab", type=int)
    no_approval = request.args.get("no_approval") == "1"

    stmt = (
        select(EquipmentType)
        .where(EquipmentType.is_archived.is_(False))
        .options(selectinload(EquipmentType.units).selectinload(EquipmentUnit.lab))
        .order_by(EquipmentType.name)
    )
    if q:
        stmt = stmt.where(or_(EquipmentType.name.ilike(f"%{q}%"), EquipmentType.category.ilike(f"%{q}%")))
    if category:
        stmt = stmt.where(EquipmentType.category == category)
    if lab_id:
        stmt = stmt.where(EquipmentType.units.any(
            (EquipmentUnit.lab_id == lab_id) & (EquipmentUnit.status != UnitStatus.RETIRED)
        ))
    if no_approval:
        stmt = stmt.where(EquipmentType.requires_approval.is_(False))

    types = db.session.scalars(stmt).all()

    # "Free between [time]–[time]" (FR-E2): on the chosen date, in the lab timezone.
    free_date = request.args.get("free_date", "")
    free_from = request.args.get("free_from", "")
    free_to = request.args.get("free_to", "")
    free_error = None
    if free_from or free_to:
        try:
            day = date.fromisoformat(free_date) if free_date else utcnow().astimezone(default_tz()).date()
            start = datetime.combine(day, time.fromisoformat(free_from), default_tz())
            end = datetime.combine(day, time.fromisoformat(free_to), default_tz())
            if end <= start:
                raise ValueError
        except ValueError:
            free_error = "Enter a valid time range."
        else:
            cache = LabDayCache()
            types = [t for t in types if free_units(t, start, end, lab_days=cache)]

    now = utcnow()
    status = {t.id: now_status(t, now) for t in types}
    categories = db.session.scalars(
        select(EquipmentType.category).where(EquipmentType.is_archived.is_(False)).distinct()
        .order_by(EquipmentType.category)
    ).all()
    labs = db.session.scalars(select(Lab).order_by(Lab.name)).all()
    filters_active = bool(q or category or lab_id or no_approval or free_from or free_to)
    return render_template("catalogue/index.html", types=types, categories=categories, labs=labs, q=q,
                           category=category, lab_id=lab_id, no_approval=no_approval, filters_active=filters_active,
                           status=status, free_date=free_date, free_from=free_from, free_to=free_to,
                           free_error=free_error, tz=default_tz())


@bp.get("/<int:type_id>")
@login_required
def detail(type_id: int):
    eq_type = db.get_or_404(EquipmentType, type_id)
    if eq_type.is_archived and not current_user.has_role(Role.LAB_TECH):
        abort(404)
    return render_type_page(eq_type, picker_form(eq_type))
