from flask_migrate import upgrade
from sqlalchemy import inspect, text

from labbook import create_app
from labbook.extensions import db

TRIGGERS = {
    "booking_no_overlap_insert",
    "booking_no_overlap_update",
    "maintenance_block_no_overlap_insert",
    "maintenance_block_no_overlap_update",
}


def test_migrations_build_the_full_schema(tmp_path):
    app = create_app("testing", SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'm.db'}")
    with app.app_context():
        upgrade()
        tables = set(inspect(db.engine).get_table_names())
        with db.engine.connect() as conn:
            triggers = set(conn.scalars(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")))
        db.engine.dispose()
    assert set(db.metadata.tables) <= tables
    assert triggers == TRIGGERS


def test_create_all_installs_the_same_triggers(app):
    triggers = set(db.session.scalars(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")))
    assert triggers == TRIGGERS
