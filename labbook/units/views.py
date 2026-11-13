"""Units & Maintenance for Lab Techs and Admins (FR-E4, spec §4.9).

Unit status changes, maintenance blocks and log entries go through services/maintenance.py, which moves or
cancels affected bookings and tells the bookers.
"""

from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ..admin.forms import UnitForm
from ..extensions import db
from ..models import (
    ACTIVE_STATUSES,
    Booking,
    EquipmentType,
    EquipmentUnit,
    Lab,
    LogKind,
    MaintenanceBlock,
    MaintenanceLog,
    Role,
    UnitStatus,
)
from ..permissions import role_required, safe_next
from ..services import catalogue as cat
from ..services import maintenance
from ..services.availability import in_service_units
from ..services.bookings import BookingRefused
from ..timeutil import default_tz, utcnow

bp = Blueprint("units", __name__, url_prefix="/units")

MANUAL_LOG_KINDS = (LogKind.ISSUE, LogKind.REPAIR, LogKind.CALIBRATION)

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
        elif unit is not None and form.type_id.data != unit.type_id and _has_future_bookings(unit):
            form.type_id.errors.append("This unit has upcoming bookings; it can't change type until they're over.")
        else:
            target = unit or EquipmentUnit()
            cat.save(current_user, target, cat.UNIT_FIELDS, {
                "type_id": form.type_id.data,
                "lab_id": form.lab_id.data,
                "asset_tag": tag,
                "serial_no": (form.serial_no.data or "").strip(),
                "notes": form.notes.data or "",
            }, "EquipmentUnit")
            db.session.commit()
            flash(f"Unit {target.asset_tag} saved.", "success")
            return redirect(url_for("units.detail", unit_id=target.id))
    crumbs = [("Units", url_for("units.index"))]
    if unit is not None:
        crumbs.append((unit.asset_tag, url_for("units.detail", unit_id=unit.id)))
    return render_template("form_page.html", form=form, title=f"Edit {unit.asset_tag}" if unit else "Add unit",
                           cancel_url=url_for("units.detail", unit_id=unit.id) if unit else url_for("units.index"),
                           crumbs=crumbs)


def _has_future_bookings(unit: EquipmentUnit) -> bool:
    return db.session.scalar(
        select(Booking.id).where(Booking.unit_id == unit.id, Booking.status.in_(ACTIVE_STATUSES),
                                 Booking.ends_at > utcnow()).limit(1)
    ) is not None


@bp.route("/new", methods=["GET", "POST"])
@role_required(Role.LAB_TECH)
def new():
    return _unit_page(None)


@bp.route("/<int:unit_id>/edit", methods=["GET", "POST"])
@role_required(Role.LAB_TECH)
def edit(unit_id: int):
    return _unit_page(db.get_or_404(EquipmentUnit, unit_id))


@bp.get("/<int:unit_id>")
@role_required(Role.LAB_TECH)
def detail(unit_id: int):
    unit = db.get_or_404(EquipmentUnit, unit_id)
    now = utcnow()
    logs = db.session.scalars(
        select(MaintenanceLog).where(MaintenanceLog.unit_id == unit.id)
        .order_by(MaintenanceLog.created_at.desc(), MaintenanceLog.id.desc()).limit(50)
    ).all()
    blocks = db.session.scalars(
        select(MaintenanceBlock).where(MaintenanceBlock.unit_id == unit.id, MaintenanceBlock.ends_at > now)
        .order_by(MaintenanceBlock.starts_at)
    ).all()
    upcoming = db.session.scalars(
        select(Booking).where(Booking.unit_id == unit.id, Booking.status.in_(ACTIVE_STATUSES), Booking.ends_at > now)
        .order_by(Booking.starts_at).limit(10)
    ).all()
    return render_template("units/detail.html", unit=unit, logs=logs, blocks=blocks, upcoming=upcoming,
                           tz=default_tz(), statuses=list(UnitStatus), log_kinds=MANUAL_LOG_KINDS)


@bp.post("/<int:unit_id>/status")
@role_required(Role.LAB_TECH)
def set_status(unit_id: int):
    unit = db.get_or_404(EquipmentUnit, unit_id)
    status_value = request.form.get("status", "")
    if status_value not in UnitStatus.__members__:
        abort(400)
    try:
        result = maintenance.set_unit_status(unit, UnitStatus(status_value), request.form.get("reason", ""),
                                             current_user)
    except BookingRefused as refused:
        db.session.rollback()
        flash(refused.violations[0].message, "error")
    else:
        db.session.commit()
        note = f" {result.summary().capitalize()}." if result.summary() else ""
        flash(f"{unit.asset_tag} is now {unit.status.label.lower()}.{note}", "success")
    return redirect(url_for("units.detail", unit_id=unit.id))


def _parse_local(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=default_tz()) if value else None
    except ValueError:
        return None


@bp.post("/<int:unit_id>/blocks")
@role_required(Role.LAB_TECH)
def add_block(unit_id: int):
    """FR-M2: block this unit, or every in-service unit of its type."""
    unit = db.get_or_404(EquipmentUnit, unit_id)
    start, end = _parse_local(request.form.get("starts_at")), _parse_local(request.form.get("ends_at"))
    if start is None or end is None:
        flash("Enter a start and end time for the block.", "error")
        return redirect(url_for("units.detail", unit_id=unit.id))
    units = in_service_units(unit.type) if request.form.get("scope") == "type" else [unit]
    if unit not in units:
        units.append(unit)
    try:
        blocks, result = maintenance.create_blocks(units, start, end, request.form.get("reason", ""), current_user)
    except BookingRefused as refused:
        db.session.rollback()
        flash(refused.violations[0].message, "error")
    else:
        db.session.commit()
        note = f" {result.summary().capitalize()}." if result.summary() else ""
        flash(f"Blocked {len(blocks)} unit{'s' if len(blocks) != 1 else ''}.{note}", "success")
    return redirect(url_for("units.detail", unit_id=unit.id))


@bp.post("/blocks/<int:block_id>/delete")
@role_required(Role.LAB_TECH)
def delete_block(block_id: int):
    block = db.get_or_404(MaintenanceBlock, block_id)
    unit_id = block.unit_id
    maintenance.delete_block(block, current_user)
    db.session.commit()
    flash("Maintenance block removed.", "success")
    return redirect(url_for("units.detail", unit_id=unit_id))


@bp.post("/<int:unit_id>/log")
@role_required(Role.LAB_TECH)
def add_log(unit_id: int):
    unit = db.get_or_404(EquipmentUnit, unit_id)
    kind = request.form.get("kind", "")
    notes = request.form.get("notes", "").strip()
    if kind not in {k.value for k in MANUAL_LOG_KINDS} or not notes:
        flash("Choose an entry type and add notes.", "error")
    else:
        maintenance.add_log(unit, LogKind(kind), notes, current_user)
        db.session.commit()
        flash("Log entry added.", "success")
    return redirect(url_for("units.detail", unit_id=unit.id))


@bp.post("/log/<int:log_id>/resolve")
@role_required(Role.LAB_TECH)
def resolve_log(log_id: int):
    entry = db.get_or_404(MaintenanceLog, log_id)
    maintenance.resolve_log(entry, current_user)
    db.session.commit()
    flash("Issue marked resolved.", "success")
    return redirect(safe_next(request.form.get("next")) or url_for("units.detail", unit_id=entry.unit_id))
