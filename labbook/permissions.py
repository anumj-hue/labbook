from functools import wraps

from flask import abort, request
from flask_login import current_user, login_required

from .models import Role


def role_required(*roles: Role):
    """Restricts a view to the given roles (Admins are always allowed, spec §2.1)."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_role(*roles):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def client_ip() -> str:
    return request.remote_addr or "unknown"


def safe_next(target: str | None) -> str | None:
    """Only allow same-site relative redirects."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None
