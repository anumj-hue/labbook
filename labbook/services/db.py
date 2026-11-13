from contextlib import contextmanager

from ..extensions import db


@contextmanager
def write_transaction():
    """Starts a SQLite BEGIN IMMEDIATE transaction for the current session.

    IMMEDIATE takes the database write lock up front, so concurrent booking requests are serialised and
    a check-then-insert inside this block can't race (spec §7.3, FR-B6). Any transaction already open
    on the session (normally just the request's reads) is committed first. The caller commits.
    """
    if db.session().in_transaction():
        db.session.commit()
    db.session.connection(execution_options={"sqlite_begin": "IMMEDIATE"})
    yield
