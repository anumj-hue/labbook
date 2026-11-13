from datetime import date, timedelta

from flask import Blueprint, Response, abort, jsonify, request

from .extensions import csrf, db
from .jobs import secret_matches, tick
from .models import Role
from .permissions import role_required
from .services import reports
from .timeutil import local_today

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.post("/jobs/tick")
@csrf.exempt
def jobs_tick():
    """Cron-triggered job runner (spec §7.7). Needs the X-Jobs-Secret header; 404 if not configured."""
    if not secret_matches(request.headers.get("X-Jobs-Secret")):
        abort(404)
    return jsonify(tick())


@bp.get("/export/bookings.csv")
@role_required(Role.LAB_TECH)
def export_bookings():
    """GET /api/export/bookings.csv?from=YYYY-MM-DD&to=YYYY-MM-DD (spec §7.7, FR-R6)."""
    try:
        today = local_today()
        start = date.fromisoformat(request.args["from"]) if request.args.get("from") else today - timedelta(days=30)
        end = date.fromisoformat(request.args["to"]) if request.args.get("to") else today
    except ValueError:
        abort(400)
    rows = db.session.scalars(reports.bookings_query(min(start, end), max(start, end))).all()
    filename = f"bookings-{min(start, end)}-to-{max(start, end)}.csv"
    return Response(reports.bookings_csv(rows), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
