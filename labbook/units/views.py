"""Units list and editing for Lab Techs and Admins (FR-E4).

Out-of-service reassignment, maintenance blocks and logs arrive with slice 4.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ..admin.forms import UnitForm
from ..extensions import db
from ..models import EquipmentType, EquipmentUnit, Lab, Role, UnitStatus
from ..permissions import role_required
from ..services import catalogue as cat

bp = Blueprint("units", __name__, url_prefix="/units")

PER_PAGE = 25


@bp.get("/")
@role_required(Role.LAB_TECH)
def index():
    q = request.args.get("q", "").strip()
    type_id = request.args.get("type", type=int)
    lab_id = request.args.get("lab", type=int)
    status = request.args.get("status", "")
    stmt = (
        select(EquipmentUnit)
        .join(EquipmentUnit.type)
        .options(joinedload(EquipmentUnit.type), joinedload(EquipmentUnit.lab))
        .order_by(EquipmentType.name, EquipmentUnit.asset_tag)
    )
    if q:
        stmt = stmt.where(EquipmentUnit.asset_tag.ilike(f"%{q}%") | EquipmentUnit.serial_no.ilike(f"%{q}%"))
    if type_id:
        stmt = stmt.where(EquipmentUnit.type_id == type_id)
    if lab_id:
        stmt = stmt.where(EquipmentUnit.lab_id == lab_id)
    if status in UnitStatus.__members__:
        stmt = stmt.where(EquipmentUnit.status == UnitStatus(status))
    else:
        stmt = stmt.where(EquipmentUnit.status != UnitStatus.RETIRED)
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    return render_template("units/index.html", page=page, q=q, type_id=type_id, lab_id=lab_id, status=status,
                           types=_types(include_archived=True), labs=_labs())


def _types(include_archived: bool = False) -> list[EquipmentType]:
    stmt = select(EquipmentType).order_by(EquipmentType.name)
    if not include_archived:
        stmt = stmt.where(EquipmentType.is_archived.is_(False))
    return db.session.scalars(stmt).all()


def _labs() -> list[Lab]:
    return db.session.scalars(select(Lab).order_by(Lab.name)).all()


def _unit_page(unit: EquipmentUnit | None):
    form = UnitForm(obj=unit)
    if unit is None and request.method == "GET" and request.args.get("type", type=int):
        form.type_id.data = request.args.get("type", type=int)
    types = _types()
    if unit is not None and unit.type.is_archived:
        types.append(unit.type)
    form.type_id.choices = [(t.id, t.name) for t in types]
    form.lab_id.choices = [(lab.id, lab.name) for lab in _labs()]
    if not form.lab_id.choices:
        flash("Add a lab before adding units.", "error")
        return redirect(url_for("admin.lab_new") if current_user.role is Role.ADMIN else url_for("units.index"))
    if form.validate_on_submit():
        tag = form.asset_tag.data.strip().upper()
        if cat.asset_tag_taken(tag, unit.id if unit else None):
            form.asset_tag.errors.append("Another unit already has this asset tag.")
        else:
            target = unit or EquipmentUnit()
            cat.save(current_user, target, cat.UNIT_FIELDS, {
                "type_id": form.type_id.data,
                "lab_id": form.lab_id.data,
                "asset_tag": tag,
                "serial_no": (form.serial_no.data or "").strip(),
                "notes": form.notes.data or "",
                "status": form.status.data,
            }, "EquipmentUnit")
            db.session.commit()
            flash(f"Unit {target.asset_tag} saved.", "success")
            return redirect(url_for("units.index"))
    return render_template("form_page.html", form=form, title=f"Edit {unit.asset_tag}" if unit else "Add unit",
                           cancel_url=url_for("units.index"), crumbs=[("Units", url_for("units.index"))])


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.LAB_TECH)
def new():
    return _unit_page(None)


@bp.route("/<int:unit_id>", methods=["GET", "POST"])
@role_required(Role.LAB_TECH)
def edit(unit_id: int):
    return _unit_page(db.get_or_404(EquipmentUnit, unit_id))
