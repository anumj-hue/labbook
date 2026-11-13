import logging
import sqlite3
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import DEV_SECRET_KEY, get_config
from .extensions import csrf, db, login_manager, migrate


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def create_app(config_name: str | None = None, **overrides) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))
    app.config.update(overrides)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + str(Path(app.instance_path) / "labbook.db")
    if app.config["ENV_NAME"] == "production" and app.config["SECRET_KEY"] == DEV_SECRET_KEY:
        raise RuntimeError("Set SECRET_KEY before running in production.")
    if app.config["TRUST_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    _init_login(app)

    from . import models  # noqa: F401  (register models with SQLAlchemy metadata)
    from .admin.views import bp as admin_bp
    from .auth.views import bp as auth_bp
    from .catalogue.views import bp as catalogue_bp
    from .cli import register_cli
    from .main.views import bp as main_bp
    from .units.views import bp as units_bp

    for bp in (auth_bp, main_bp, catalogue_bp, units_bp, admin_bp):
        app.register_blueprint(bp)

    register_cli(app)
    _init_templates(app)
    _init_consent_gate(app)
    _init_error_pages(app)
    return app


def _init_login(app: Flask) -> None:
    from .services.auth import load_user

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Sign in to continue."
    login_manager.login_message_category = "info"
    login_manager.session_protection = "basic"
    login_manager.user_loader(load_user)


def _init_templates(app: Flask) -> None:
    from markdown_it import MarkdownIt

    from .models import Role, UnitStatus
    from .nav import nav_sections
    from .timeutil import fmt_date, fmt_datetime

    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True})

    @app.template_filter("markdown")
    def markdown_filter(text: str | None) -> Markup:
        return Markup(md.render(text or ""))  # raw HTML disabled, so output is safe

    app.add_template_filter(fmt_date, "date")
    app.add_template_filter(fmt_datetime, "datetime")
    app.jinja_env.globals.update(Role=Role, UnitStatus=UnitStatus, nav_sections=nav_sections)
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True


def _init_consent_gate(app: Flask) -> None:
    """FR-D1: after a new privacy notice is published, users must accept it before continuing."""
    from .services.privacy import needs_consent

    exempt = {"static", "auth.logout", "auth.privacy", "auth.accept_privacy"}

    @app.before_request
    def require_current_consent():
        if request.endpoint in exempt or not current_user.is_authenticated:
            return None
        if needs_consent(current_user):
            return redirect(url_for("auth.accept_privacy", next=request.full_path))
        return None


def _init_error_pages(app: Flask) -> None:
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/error.html", code=403, title="You don't have access to this page"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/error.html", code=404, title="Page not found"), 404

    @app.errorhandler(500)
    def server_error(_e):
        db.session.rollback()
        return render_template("errors/error.html", code=500, title="Something went wrong"), 500
