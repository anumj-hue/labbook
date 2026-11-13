from flask import Blueprint, abort, jsonify, request

from .extensions import csrf
from .jobs import secret_matches, tick

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.post("/jobs/tick")
@csrf.exempt
def jobs_tick():
    """Cron-triggered job runner (spec §7.7). Needs the X-Jobs-Secret header; 404 if not configured."""
    if not secret_matches(request.headers.get("X-Jobs-Secret")):
        abort(404)
    return jsonify(tick())
