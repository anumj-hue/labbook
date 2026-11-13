from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import EquipmentType, EquipmentUnit, Lab, Role, UnitStatus
from ..services import settings

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
    categories = db.session.scalars(
        select(EquipmentType.category).where(EquipmentType.is_archived.is_(False)).distinct()
        .order_by(EquipmentType.category)
    ).all()
    labs = db.session.scalars(select(Lab).order_by(Lab.name)).all()
    filters_active = bool(q or category or lab_id or no_approval)
    return render_template("catalogue/index.html", types=types, categories=categories, labs=labs, q=q,
                           category=category, lab_id=lab_id, no_approval=no_approval, filters_active=filters_active)


@bp.get("/<int:type_id>")
@login_required
def detail(type_id: int):
    eq_type = db.get_or_404(EquipmentType, type_id)
    if eq_type.is_archived and not current_user.has_role(Role.LAB_TECH):
        abort(404)
    units = [u for u in eq_type.units if u.status is not UnitStatus.RETIRED]
    return render_template("catalogue/detail.html", eq_type=eq_type, units=units,
                           rules=settings.effective_rules(eq_type))
