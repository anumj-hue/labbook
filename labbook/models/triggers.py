"""SQLite triggers that make double-booking impossible (spec §7.3, adapted for SQLite).

SQLite has no exclusion constraints, so BEFORE INSERT/UPDATE triggers abort any write that would give a
unit two overlapping active bookings, a booking overlapping a maintenance block, or two overlapping
blocks. Ranges are half-open [start, end). Booking writes also run under BEGIN IMMEDIATE
(services/db.py), so these triggers are the backstop, not the main check.

The SQL is applied both by db.create_all() (tests) and by the Alembic migration.
"""

from sqlalchemy import DDL, event

from ..extensions import db
from .booking import ACTIVE_STATUSES

BOOKING_OVERLAP = "booking_overlap"
MAINTENANCE_OVERLAP = "maintenance_overlap"
BLOCK_OVERLAP = "maintenance_block_overlap"

_ACTIVE = ", ".join(f"'{s.value}'" for s in ACTIVE_STATUSES)

_BOOKING_CHECKS = f"""
  SELECT RAISE(ABORT, '{BOOKING_OVERLAP}') WHERE EXISTS (
    SELECT 1 FROM booking b
    WHERE b.unit_id = NEW.unit_id AND b.id IS NOT NEW.id AND b.status IN ({_ACTIVE})
      AND b.starts_at < NEW.ends_at AND NEW.starts_at < b.ends_at);
  SELECT RAISE(ABORT, '{MAINTENANCE_OVERLAP}') WHERE EXISTS (
    SELECT 1 FROM maintenance_block m
    WHERE m.unit_id = NEW.unit_id AND m.starts_at < NEW.ends_at AND NEW.starts_at < m.ends_at);
"""

_BLOCK_CHECK = f"""
  SELECT RAISE(ABORT, '{BLOCK_OVERLAP}') WHERE EXISTS (
    SELECT 1 FROM maintenance_block m
    WHERE m.unit_id = NEW.unit_id AND m.id IS NOT NEW.id
      AND m.starts_at < NEW.ends_at AND NEW.starts_at < m.ends_at);
"""

CREATE_STATEMENTS = (
    f"""CREATE TRIGGER booking_no_overlap_insert BEFORE INSERT ON booking
WHEN NEW.status IN ({_ACTIVE})
BEGIN{_BOOKING_CHECKS}END""",
    # Only re-check when a booking becomes active or moves; a status change between two active
    # statuses (e.g. Confirmed -> Checked out) must not fail because of a block added later.
    f"""CREATE TRIGGER booking_no_overlap_update BEFORE UPDATE OF unit_id, starts_at, ends_at, status ON booking
WHEN NEW.status IN ({_ACTIVE}) AND (OLD.status NOT IN ({_ACTIVE}) OR NEW.unit_id IS NOT OLD.unit_id
  OR NEW.starts_at IS NOT OLD.starts_at OR NEW.ends_at IS NOT OLD.ends_at)
BEGIN{_BOOKING_CHECKS}END""",
    f"""CREATE TRIGGER maintenance_block_no_overlap_insert BEFORE INSERT ON maintenance_block
BEGIN{_BLOCK_CHECK}END""",
    f"""CREATE TRIGGER maintenance_block_no_overlap_update BEFORE UPDATE OF unit_id, starts_at, ends_at
ON maintenance_block
BEGIN{_BLOCK_CHECK}END""",
)

DROP_STATEMENTS = (
    "DROP TRIGGER IF EXISTS booking_no_overlap_insert",
    "DROP TRIGGER IF EXISTS booking_no_overlap_update",
    "DROP TRIGGER IF EXISTS maintenance_block_no_overlap_insert",
    "DROP TRIGGER IF EXISTS maintenance_block_no_overlap_update",
)

for _statement in CREATE_STATEMENTS:
    event.listen(db.metadata, "after_create", DDL(_statement).execute_if(dialect="sqlite"))
