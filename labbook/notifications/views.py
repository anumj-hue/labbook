"""Notification list and bell count (spec §4.10, §6.10)."""

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select, update

from ..extensions import db
from ..models import Notification
from ..permissions import safe_next
from ..timeutil import utcnow

bp = Blueprint("notifications", __name__, url_prefix="/notifications")

PER_PAGE = 25


def unread_count() -> int:
    if not current_user.is_authenticated:
        return 0
    return db.session.scalar(
        select(func.count()).select_from(Notification)
        .where(Notification.user_id == current_user.id, Notification.read_at.is_(None))
    )


@bp.get("/")
@login_required
def index():
    stmt = (select(Notification).where(Notification.user_id == current_user.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc()))
    page = db.paginate(stmt, per_page=PER_PAGE, error_out=False)
    return render_template("notifications/index.html", page=page)


@bp.get("/<int:notification_id>")
@login_required
def open_(notification_id: int):
    """Marks one notification read and follows its link."""
    item = db.get_or_404(Notification, notification_id)
    if item.user_id != current_user.id:
        abort(404)
    if item.read_at is None:
        item.read_at = utcnow()
        db.session.commit()
    return redirect(safe_next(item.url) or url_for("notifications.index"))


@bp.post("/read-all")
@login_required
def read_all():
    db.session.execute(
        update(Notification).where(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("notifications.index"))
