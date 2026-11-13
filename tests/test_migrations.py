from flask_migrate import upgrade
from sqlalchemy import inspect

from labbook import create_app
from labbook.extensions import db


def test_migrations_build_the_full_schema(tmp_path):
    app = create_app("testing", SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'm.db'}")
    with app.app_context():
        upgrade()
        tables = set(inspect(db.engine).get_table_names())
        db.engine.dispose()
    assert set(db.metadata.tables) <= tables
