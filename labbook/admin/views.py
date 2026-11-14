from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select

from ..extensions import db
from ..models import Accessory, AuditLog, EquipmentType, Holiday, Lab, Role, Term, User
from ..services import catalogue as cat
from ..services import closures, mail, notify, privacy, settings, users
from ..services.settings import RULES
from .forms import (
    AccessoryForm,
    EquipmentTypeForm,
    HolidayForm,
    LabForm,
    PrivacyNoticeForm,
    SettingsForm,
    TermForm,
    UserEditForm,
    UserInviteForm,
    hours_from_form,
    hours_to_form_data,
    parse_domains,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")

PER_PAGE = 25


@bp.before_request
@login_required
def admins_only():
    if current_user.role is not Role.ADMIN:
        abort(403)


def _get_or_404(model, obj_id):
    return db.get_or_404(model, obj_id)


def _closure_gate(affected: list, reason: str) -> tuple[bool, dict | None, int]:
    """FR-T4: when a change invalidates future bookings, show the count first and cancel only once confirmed.

    Returns (proceed, warning, cancelled). On a first submit with affected bookings the flushed change is
    rolled back and `warning` describes what would happen; the form re-posts with confirm_cancel=<count>.
    """
    if not affected:
        return True, None, 0
    if request.form.get("confirm_cancel") == str(len(affected)):
        return True, None, closures.cancel_affected(affected, current_user, reason)
    warning = {
        "count": len(affected),
        "items": [f"{b.type.name} ({b.unit.asset_tag}) · {notify.when(b)} · {b.user.name if b.user else 'Unknown'}"
                  for b in affected[:5]],
    }
    db.session.rollback()
    return False, warning, 0


def _cancelled_note(count: int) -> str:
    return f" {count} booking{'s' if count != 1 else ''} cancelled and the bookers told." if count else ""


# --- Users ----------------------------------------------------------------------------------------

@bp.get("/users", endpoint="users")
def users_list():
    q = request.args.get("q", "").strip()
    role = request.args.get("role", "")
    status = request.args.get("status", "")
    stmt = select(User).where(User.anonymised_at.is_(None)).order_by(User.name)
    if q:
        stmt = stmt.where(or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    if role in Role.__members__:
        stmt = stmt.where(User.role == Role(role))
    if status == "active":
        stmt = stmt.where(User.active.is_(True))
    elif status == "deactivated":
        stmt = stmt.where(User.active.is_(False))
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    return render_template("admin/users.html", page=page, q=q, role=role, status=status)



@bp.route("/users/new", methods=["GET", "POST"])
def user_new():
    form = UserInviteForm()
    if form.validate_on_submit():
        try:
            user = users.invite(current_user, form.name.data, form.email.data, form.role.data)
        except users.UserError as err:
            form.email.errors.append(str(err))
        else:
            db.session.commit()
            sent = users.send_invite(user)
            flash(f"Invite sent to {user.email}." if sent else "User created, but the invite email failed to send.",
                  "success" if sent else "error")
            return redirect(url_for("admin.users"))
    return render_template("form_page.html", form=form, title="Invite user", submit_label="Send invite",
                           cancel_url=url_for("admin.users"), crumbs=[("Users", url_for("admin.users"))],
                           intro="They'll get an email with a link to set their password. The link lasts 72 hours.")


@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
def user_edit(user_id: int):
    user = _get_or_404(User, user_id)
    form = UserEditForm(obj=user)
    if form.validate_on_submit():
        try:
            users.update(current_user, user, form.name.data, form.role.data)
        except users.UserError as err:
            form.role.errors.append(str(err))
        else:
            db.session.commit()
            flash(f"{user.name} updated.", "success")
            return redirect(url_for("admin.users"))
    return render_template("admin/user_edit.html", form=form, user=user)


@bp.post("/users/<int:user_id>/<action>")
def user_action(user_id: int, action: str):
    user = _get_or_404(User, user_id)
    try:
        if action == "deactivate":
            cancelled = users.deactivate(current_user, user)
            message = f"{user.name} has been deactivated and signed out."
            if cancelled:
                message += f" {cancelled} future booking{'s' if cancelled != 1 else ''} cancelled."
        elif action == "reactivate":
            users.reactivate(current_user, user)
            message = f"{user.name} has been reactivated."
        elif action == "lift-suspension":
            users.lift_suspension(current_user, user)
            message = f"{user.name}'s suspension has been lifted."
        elif action == "verify-email":
            users.admin_verify(current_user, user)
            message = f"{user.name}'s email address is now marked as verified."
        elif action == "resend-invite":
            if user.password_hash is not None:
                raise users.UserError("This user has already set a password.")
            db.session.commit()
            users.send_invite(user)
            message = f"Invite re-sent to {user.email}."
        else:
            abort(404)
    except users.UserError as err:
        db.session.rollback()
        flash(str(err), "error")
    else:
        db.session.commit()
        flash(message, "success")
    return redirect(url_for("admin.user_edit", user_id=user.id))


# --- Labs -----------------------------------------------------------------------------------------

@bp.get("/labs")
def labs():
    rows = db.session.scalars(select(Lab).order_by(Lab.name)).all()
    return render_template("admin/labs.html", labs=rows)


def _lab_form_page(lab: Lab | None):
    if lab is None:
        form = LabForm()
    elif request.method == "GET":
        data = {"name": lab.name, "location": lab.location, "timezone": lab.timezone}
        for prefix, hours in (("term", lab.term_hours), ("vacation", lab.vacation_hours)):
            for day, values in hours_to_form_data(hours).items():
                data[f"{prefix}_{day}"] = values
        form = LabForm(data=data)
    else:
        form = LabForm()
    warning = None
    if form.validate_on_submit():
        name = form.name.data.strip()
        if cat.name_taken(Lab, name, lab.id if lab else None):
            form.name.errors.append("Another lab already has this name.")
        else:
            target = lab or Lab()
            cat.save(current_user, target, cat.LAB_FIELDS, {
                "name": name,
                "location": (form.location.data or "").strip(),
                "timezone": form.timezone.data.strip(),
                "term_hours": hours_from_form(form, "term"),
                "vacation_hours": hours_from_form(form, "vacation"),
            }, "Lab")
            affected = closures.affected_bookings(lab_id=target.id) if lab is not None else []
            proceed, warning, cancelled = _closure_gate(affected, f"{name} opening hours changed")
            if proceed:
                db.session.commit()
                flash(f"{name} saved.{_cancelled_note(cancelled)}", "success")
                return redirect(url_for("admin.labs"))
    return render_template("admin/lab_form.html", form=form, lab=lab, warning=warning)


@bp.route("/labs/new", methods=["GET", "POST"])
def lab_new():
    return _lab_form_page(None)


@bp.route("/labs/<int:lab_id>", methods=["GET", "POST"])
def lab_edit(lab_id: int):
    return _lab_form_page(_get_or_404(Lab, lab_id))


# --- Academic calendar ----------------------------------------------------------------------------

@bp.get("/calendar")
def calendar():
    terms = db.session.scalars(select(Term).order_by(Term.start_date.desc())).all()
    holidays = db.session.scalars(select(Holiday).order_by(Holiday.start_date.desc())).all()
    return render_template("admin/calendar.html", terms=terms, holidays=holidays)


def _term_page(term: Term | None):
    form = TermForm(obj=term)
    warning = None
    if form.validate_on_submit():
        try:
            cat.check_term_overlap(form.start_date.data, form.end_date.data, term.id if term else None)
        except cat.CatalogueError as err:
            form.start_date.errors.append(str(err))
        else:
            target = term or Term()
            name = form.name.data.strip()
            cat.save(current_user, target, cat.TERM_FIELDS, {
                "name": name, "start_date": form.start_date.data, "end_date": form.end_date.data,
            }, "Term")
            proceed, warning, cancelled = _closure_gate(closures.affected_bookings(), f"Term {name} changed")
            if proceed:
                db.session.commit()
                flash(f"Term {name} saved.{_cancelled_note(cancelled)}", "success")
                return redirect(url_for("admin.calendar"))
    crumbs = [("Academic calendar", url_for("admin.calendar"))]
    return render_template("form_page.html", form=form, title="Edit term" if term else "Add term",
                           cancel_url=url_for("admin.calendar"), crumbs=crumbs,
                           delete_url=url_for("admin.calendar_term_delete", term_id=term.id) if term else None,
                           warning=warning,
                           intro="Term opening hours apply to dates inside a term; "
                                 "vacation hours apply outside every term.")


@bp.route("/calendar/terms/new", methods=["GET", "POST"])
def calendar_term_new():
    return _term_page(None)


@bp.route("/calendar/terms/<int:term_id>", methods=["GET", "POST"])
def calendar_term_edit(term_id: int):
    return _term_page(_get_or_404(Term, term_id))


@bp.post("/calendar/terms/<int:term_id>/delete")
def calendar_term_delete(term_id: int):
    term = _get_or_404(Term, term_id)
    name = term.name
    cat.delete(current_user, term, cat.TERM_FIELDS, "Term")
    db.session.flush()
    proceed, warning, cancelled = _closure_gate(closures.affected_bookings(), f"Term {name} removed")
    if not proceed:
        return render_template("admin/confirm_closure.html", warning=warning, title=f"Delete term {name}?",
                               action=url_for("admin.calendar_term_delete", term_id=term_id),
                               cancel_url=url_for("admin.calendar_term_edit", term_id=term_id),
                               confirm_label="Delete term and cancel bookings")
    db.session.commit()
    flash(f"Term deleted.{_cancelled_note(cancelled)}", "success")
    return redirect(url_for("admin.calendar"))


def _holiday_page(holiday: Holiday | None):
    form = HolidayForm(obj=holiday)
    if holiday is not None and request.method == "GET":
        form.lab_id.data = holiday.lab_id or 0
    form.lab_id.choices = [(0, "All labs")] + [
        (lab.id, lab.name) for lab in db.session.scalars(select(Lab).order_by(Lab.name))
    ]
    warning = None
    if form.validate_on_submit():
        target = holiday or Holiday()
        start, end = form.start_date.data, form.end_date.data or form.start_date.data
        reason = form.reason.data.strip()
        cat.save(current_user, target, cat.HOLIDAY_FIELDS, {
            "lab_id": form.lab_id.data or None, "start_date": start, "end_date": end, "reason": reason,
        }, "Holiday")
        affected = closures.affected_bookings(start, end, form.lab_id.data or None)
        proceed, warning, cancelled = _closure_gate(affected, f"Lab closed: {reason}")
        if proceed:
            db.session.commit()
            flash(f"Closure saved.{_cancelled_note(cancelled)}", "success")
            return redirect(url_for("admin.calendar"))
    crumbs = [("Academic calendar", url_for("admin.calendar"))]
    return render_template(
        "form_page.html", form=form, title="Edit holiday or closure" if holiday else "Add holiday or closure",
        cancel_url=url_for("admin.calendar"), crumbs=crumbs,
        delete_url=url_for("admin.calendar_holiday_delete", holiday_id=holiday.id) if holiday else None,
        intro="Nothing can be booked in a closed lab on these dates.", warning=warning,
    )


@bp.route("/calendar/holidays/new", methods=["GET", "POST"])
def calendar_holiday_new():
    return _holiday_page(None)


@bp.route("/calendar/holidays/<int:holiday_id>", methods=["GET", "POST"])
def calendar_holiday_edit(holiday_id: int):
    return _holiday_page(_get_or_404(Holiday, holiday_id))


@bp.post("/calendar/holidays/<int:holiday_id>/delete")
def calendar_holiday_delete(holiday_id: int):
    holiday = _get_or_404(Holiday, holiday_id)
    cat.delete(current_user, holiday, cat.HOLIDAY_FIELDS, "Holiday")
    db.session.commit()
    flash("Closure deleted.", "success")
    return redirect(url_for("admin.calendar"))


# --- Equipment types & accessories ----------------------------------------------------------------

@bp.get("/types")
def types():
    show_archived = request.args.get("archived") == "1"
    stmt = select(EquipmentType).order_by(EquipmentType.category, EquipmentType.name)
    if not show_archived:
        stmt = stmt.where(EquipmentType.is_archived.is_(False))
    return render_template("admin/types.html", types=db.session.scalars(stmt).all(), show_archived=show_archived)


def _type_values(form: EquipmentTypeForm) -> dict:
    return {
        "name": form.name.data.strip(),
        "category": form.category.data.strip(),
        "description": form.description.data or "",
        "safety_notes": form.safety_notes.data or "",
        "photo_url": (form.photo_url.data or "").strip() or None,
        "requires_approval": form.requires_approval.data,
        "purpose_required": form.purpose_required.data,
        "max_duration_min": form.max_duration_min.data,
        "student_advance_days": form.student_advance_days.data,
    }


def _categories() -> list[str]:
    return list(db.session.scalars(select(EquipmentType.category).distinct().order_by(EquipmentType.category)))


@bp.route("/types/new", methods=["GET", "POST"])
def type_new():
    form = EquipmentTypeForm()
    if form.validate_on_submit():
        if cat.name_taken(EquipmentType, form.name.data.strip()):
            form.name.errors.append("Another equipment type already has this name.")
        else:
            eq_type = EquipmentType()
            cat.save(current_user, eq_type, cat.TYPE_FIELDS, _type_values(form), "EquipmentType")
            db.session.commit()
            flash(f"{eq_type.name} created. Add its accessories and units next.", "success")
            return redirect(url_for("admin.type_edit", type_id=eq_type.id))
    return render_template("admin/type_form.html", form=form, eq_type=None, categories=_categories())


@bp.route("/types/<int:type_id>", methods=["GET", "POST"])
def type_edit(type_id: int):
    eq_type = _get_or_404(EquipmentType, type_id)
    form = EquipmentTypeForm(obj=eq_type)
    if form.validate_on_submit():
        if cat.name_taken(EquipmentType, form.name.data.strip(), eq_type.id):
            form.name.errors.append("Another equipment type already has this name.")
        else:
            cat.save(current_user, eq_type, cat.TYPE_FIELDS, _type_values(form), "EquipmentType")
            db.session.commit()
            flash(f"{eq_type.name} saved.", "success")
            return redirect(url_for("admin.type_edit", type_id=eq_type.id))
    return render_template("admin/type_form.html", form=form, eq_type=eq_type, categories=_categories(),
                           accessory_form=AccessoryForm(formdata=None))


@bp.post("/types/<int:type_id>/archive")
def type_archive(type_id: int):
    eq_type = _get_or_404(EquipmentType, type_id)
    archive = not eq_type.is_archived
    cat.save(current_user, eq_type, cat.TYPE_FIELDS, {"is_archived": archive}, "EquipmentType")
    db.session.commit()
    flash(f"{eq_type.name} {'archived' if archive else 'restored'}.", "success")
    return redirect(url_for("admin.types"))


@bp.post("/types/<int:type_id>/accessories")
def type_accessory_add(type_id: int):
    eq_type = _get_or_404(EquipmentType, type_id)
    form = AccessoryForm()
    if form.validate_on_submit():
        cat.save(current_user, Accessory(type_id=eq_type.id), cat.ACCESSORY_FIELDS, {
            "name": form.name.data.strip(), "expected_qty": form.expected_qty.data,
            "sort_order": form.sort_order.data or 0,
        }, "Accessory")
        db.session.commit()
        flash("Accessory added.", "success")
    else:
        flash("Check the accessory: " + "; ".join(e for errs in form.errors.values() for e in errs), "error")
    return redirect(url_for("admin.type_edit", type_id=eq_type.id) + "#accessories")


@bp.post("/accessories/<int:accessory_id>")
def type_accessory_update(accessory_id: int):
    accessory = _get_or_404(Accessory, accessory_id)
    if request.form.get("action") == "archive":
        cat.save(current_user, accessory, cat.ACCESSORY_FIELDS, {"is_archived": not accessory.is_archived},
                 "Accessory")
        db.session.commit()
        flash("Accessory archived." if accessory.is_archived else "Accessory restored.", "success")
    else:
        form = AccessoryForm()
        if form.validate_on_submit():
            cat.save(current_user, accessory, cat.ACCESSORY_FIELDS, {
                "name": form.name.data.strip(), "expected_qty": form.expected_qty.data,
                "sort_order": form.sort_order.data or 0,
            }, "Accessory")
            db.session.commit()
            flash("Accessory saved.", "success")
        else:
            flash("Check the accessory: " + "; ".join(e for errs in form.errors.values() for e in errs), "error")
    return redirect(url_for("admin.type_edit", type_id=accessory.type_id) + "#accessories")


# --- Settings & privacy notice --------------------------------------------------------------------

@bp.route("/settings", methods=["GET", "POST"], endpoint="settings")
def settings_page():
    if request.method == "GET":
        data = {rule.key: settings.get(rule.key) for rule in RULES}
        data["allowed_email_domains"] = "\n".join(settings.get("allowed_email_domains"))
        form = SettingsForm(data=data)
    else:
        form = SettingsForm()
    if form.validate_on_submit():
        changed = 0
        for rule in RULES:
            changed += settings.set_value(rule.key, getattr(form, rule.key).data, current_user)
        changed += settings.set_value("allowed_email_domains", parse_domains(form.allowed_email_domains.data),
                                      current_user)
        settings.mark_rules_reviewed(current_user)
        db.session.commit()
        flash(f"Settings saved ({changed} change{'s' if changed != 1 else ''}).", "success")
        return redirect(url_for("admin.settings"))
    groups: dict[str, list] = {}
    for rule in RULES:
        groups.setdefault(rule.group, []).append(getattr(form, rule.key))
    return render_template("admin/settings.html", form=form, groups=groups, mail_summary=mail.settings_summary())


@bp.post("/settings/test-email")
def test_email():
    error = mail.send_test(current_user.email)
    if error:
        flash(f"Test email failed ({mail.settings_summary()}). {error}", "error")
    else:
        flash(f"Test email sent to {current_user.email}. Check the inbox and the spam folder.", "success")
    return redirect(url_for("admin.settings"))



@bp.route("/privacy", methods=["GET", "POST"], endpoint="privacy")
def privacy_page():
    notice = privacy.current_notice()
    form = PrivacyNoticeForm(data={"body_md": notice.body_md} if request.method == "GET" else None)
    if form.validate_on_submit():
        if form.body_md.data.strip() == notice.body_md.strip():
            flash("No changes to publish.", "info")
        else:
            new = privacy.publish(current_user, form.body_md.data)
            db.session.commit()
            flash(f"Version {new.version} published. Everyone will be asked to accept it at their next visit.",
                  "success")
        return redirect(url_for("admin.privacy"))
    return render_template("admin/privacy.html", form=form, notice=notice)



# --- Audit log (spec §4.12) -----------------------------------------------------------------------

@bp.get("/audit")
def audit_log():
    entity = request.args.get("entity", "")
    action = request.args.get("action", "").strip()
    actor_q = request.args.get("actor", "").strip()
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_q:
        stmt = stmt.join(User, AuditLog.actor_id == User.id).where(
            or_(User.name.ilike(f"%{actor_q}%"), User.email.ilike(f"%{actor_q}%")))
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    entities = db.session.scalars(select(AuditLog.entity).distinct().order_by(AuditLog.entity)).all()
    actions = db.session.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)).all()
    return render_template("admin/audit.html", page=page, entity=entity, action=action, actor=actor_q,
                           entities=entities, actions=actions)
