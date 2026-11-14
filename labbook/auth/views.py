from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import EMAIL_PREF_DEFAULTS
from ..permissions import client_ip, safe_next
from ..services import auth, users
from ..services import privacy as privacy_svc
from ..services.passwords import hash_password, needs_rehash, verify_password
from ..services.tokens import load_token
from .forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    ConsentForm,
    EmailPrefsForm,
    ForgotPasswordForm,
    LoginForm,
    ProfileNameForm,
    RegisterForm,
    SetPasswordForm,
)

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    form = LoginForm()
    if form.validate_on_submit():
        email = users.normalise_email(form.email.data)
        ip = client_ip()
        wait = auth.blocked_for("login", ip, email)
        if wait:
            flash(auth.wait_message(wait), "error")
            return render_template("auth/login.html", form=form), 429
        user = users.find_by_email(email)
        password_ok = verify_password(user.password_hash if user else None, form.password.data)
        allowed = password_ok and user is not None and user.is_active
        auth.record_attempt("login", ip, email, success=allowed)
        if allowed:
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(form.password.data)
            auth.start_session(user)
            db.session.commit()
            return redirect(safe_next(request.args.get("next")) or url_for("main.home"))
        db.session.commit()
        if password_ok:
            flash("This account has been deactivated. Contact a LabBook admin.", "error")
        else:
            form.password.errors.append("Incorrect email or password.")
    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    auth.end_session()
    flash("You've been signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    form = RegisterForm()
    if form.validate_on_submit():
        ip = client_ip()
        wait = auth.blocked_for("register", ip)
        if wait:
            flash(auth.wait_message(wait), "error")
            return render_template("auth/register.html", form=form, domains=users.allowed_domains()), 429
        auth.record_attempt("register", ip, users.normalise_email(form.email.data), success=True)
        try:
            user = users.register(form.name.data, form.email.data, form.password.data)
        except users.UserError as err:
            db.session.commit()  # keep the rate-limit record
            form.email.errors.append(str(err))
        else:
            auth.start_session(user)
            db.session.commit()
            if users.send_verification(user):
                flash(f"Welcome to LabBook! We've sent a verification link to {user.email}.", "success")
            else:
                flash("Your account was created, but we couldn't send the verification email. "
                      "Try “Resend” later or ask an Admin to verify your address.", "error")
            return redirect(url_for("main.home"))
    return render_template("auth/register.html", form=form, domains=users.allowed_domains())


@bp.get("/verify/<token>")
def verify_email(token: str):
    user = load_token(token, "verify")
    if user is None:
        flash("This verification link is invalid or has expired. Request a new one below.", "error")
        return redirect(url_for("main.home") if current_user.is_authenticated else url_for("auth.login"))
    users.mark_verified(user)
    db.session.commit()
    flash("Your email is verified.", "success")
    return redirect(url_for("main.home") if current_user.is_authenticated else url_for("auth.login"))


@bp.post("/verify/resend")
@login_required
def resend_verification():
    if current_user.is_verified:
        return redirect(url_for("main.home"))
    ip = client_ip()
    wait = auth.blocked_for("resend", ip, current_user.email)
    if wait:
        flash(auth.wait_message(wait), "error")
        return redirect(url_for("main.home"))
    auth.record_attempt("resend", ip, current_user.email, success=True)
    db.session.commit()
    users.send_verification(current_user)
    flash(f"We've sent a new verification link to {current_user.email}.", "success")
    return redirect(url_for("main.home"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        ip = client_ip()
        email = users.normalise_email(form.email.data)
        wait = auth.blocked_for("forgot", ip, email)
        if wait:
            flash(auth.wait_message(wait), "error")
            return render_template("auth/forgot.html", form=form), 429
        auth.record_attempt("forgot", ip, email, success=True)
        db.session.commit()
        user = users.find_by_email(email)
        if user is not None and user.is_active:
            users.send_password_reset(user)
        flash("If an account exists for that email, we've sent a reset link. It expires in 1 hour.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot.html", form=form)


def _set_password_page(token: str, purpose: str):
    user = load_token(token, purpose)
    if user is None or not user.is_active:
        flash("This link is invalid, has expired or has already been used.", "error")
        return redirect(url_for("auth.forgot_password"))
    form = SetPasswordForm()
    if form.validate_on_submit():
        users.set_password(user, form.password.data)
        users.mark_verified(user)  # the link proves ownership of the address
        if purpose == "invite":
            auth.start_session(user)
            db.session.commit()
            flash("Your password is set. Welcome to LabBook!", "success")
            return redirect(url_for("main.home"))
        db.session.commit()
        flash("Your password has been reset. Sign in with your new password.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/set_password.html", form=form, purpose=purpose, user=user)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    return _set_password_page(token, "reset")


@bp.route("/invite/<token>", methods=["GET", "POST"])
def accept_invite(token: str):
    return _set_password_page(token, "invite")


@bp.get("/privacy")
def privacy():
    return render_template("auth/privacy.html", notice=privacy_svc.current_notice())


@bp.route("/privacy/accept", methods=["GET", "POST"])
@login_required
def accept_privacy():
    if not privacy_svc.needs_consent(current_user):
        return redirect(url_for("main.home"))
    form = ConsentForm()
    if form.validate_on_submit():
        privacy_svc.record_consent(current_user)
        db.session.commit()
        flash("Thanks, you're all set.", "success")
        return redirect(safe_next(request.args.get("next")) or url_for("main.home"))
    return render_template("auth/accept_privacy.html", form=form, notice=privacy_svc.current_notice())


# --- Profile ---------------------------------------------------------------------------------------

def _profile_forms(**overrides):
    forms = {
        "name_form": ProfileNameForm(prefix="name", data={"name": current_user.name}),
        "password_form": ChangePasswordForm(prefix="pw"),
        "email_form": ChangeEmailForm(prefix="email"),
        "prefs_form": EmailPrefsForm(prefix="prefs", data={k: current_user.email_pref(k) for k in EMAIL_PREF_DEFAULTS}),
    }
    forms.update(overrides)
    return forms


@bp.get("/profile")
@login_required
def profile():
    return render_template("auth/profile.html", **_profile_forms())


@bp.post("/profile/<section>")
@login_required
def profile_update(section: str):
    if section == "name":
        form = ProfileNameForm(prefix="name")
        if form.validate_on_submit():
            current_user.name = form.name.data.strip()
            db.session.commit()
            flash("Your name has been updated.", "success")
            return redirect(url_for("auth.profile"))
        return render_template("auth/profile.html", **_profile_forms(name_form=form)), 400

    if section == "password":
        form = ChangePasswordForm(prefix="pw")
        if form.validate_on_submit():
            if not verify_password(current_user.password_hash, form.current_password.data):
                form.current_password.errors.append("That isn't your current password.")
            else:
                current_user.password_hash = hash_password(form.password.data)
                auth.revoke_sessions(current_user, keep_current=True)
                db.session.commit()
                flash("Your password has been changed. Other devices have been signed out.", "success")
                return redirect(url_for("auth.profile"))
        return render_template("auth/profile.html", **_profile_forms(password_form=form)), 400

    if section == "email":
        form = ChangeEmailForm(prefix="email")
        if form.validate_on_submit():
            new_email = users.normalise_email(form.new_email.data)
            if not verify_password(current_user.password_hash, form.current_password.data):
                form.current_password.errors.append("That isn't your current password.")
            elif not users.email_domain_allowed(new_email):
                form.new_email.errors.append(users.domain_error_message())
            elif new_email == current_user.email:
                form.new_email.errors.append("That's already your email address.")
            elif users.find_by_email(new_email):
                form.new_email.errors.append("Another account already uses this email.")
            else:
                current_user.email = new_email
                current_user.email_verified_at = None  # FR-A8: re-verify
                db.session.commit()
                users.send_verification(current_user)
                flash(f"Email updated. Check {new_email} for a verification link.", "success")
                return redirect(url_for("auth.profile"))
        return render_template("auth/profile.html", **_profile_forms(email_form=form)), 400

    if section == "notifications":
        form = EmailPrefsForm(prefix="prefs")
        if form.validate_on_submit():
            current_user.email_prefs = {k: bool(getattr(form, k).data) for k in EMAIL_PREF_DEFAULTS}
            db.session.commit()
            flash("Email preferences saved.", "success")
            return redirect(url_for("auth.profile"))
        return render_template("auth/profile.html", **_profile_forms(prefs_form=form)), 400

    abort(404)
