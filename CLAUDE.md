# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

LabBook is a Flask app for booking shared lab equipment in one department. **Slices 1–2 of 4 are built**: accounts, admin setup, catalogue, and booking (picker, create/edit/cancel, My Bookings, calendar). Students can't book approval-required equipment until slice 3 adds Pending/approvals.

- `spec.md` is the product spec and the source of truth; requirement IDs like `FR-O4` and refs like `§7.3` come from it. `spec.html` is a rendered copy; keep it in sync if you edit `spec.md`.
- The approved plan (structure + 4 MVP slices) is in `C:\Users\anumj\.claude\plans\memoized-gathering-duckling.md`.

## Commands

Windows + Git Bash; Python 3.12 venv in `.venv`. Prefix with `export PATH="$PWD/.venv/Scripts:$PATH"` or call `.venv/Scripts/...` directly.

```bash
.venv/Scripts/pip install -e ".[dev]"   # Python deps
npm install && npm run build            # Tailwind CSS + Lucide icon sprite (Node is build-time only)
npm run css:watch                       # rebuild CSS while editing templates
flask db upgrade                        # apply migrations (DB: instance/labbook.db)
flask db migrate -m "msg"               # autogenerate a migration; review it before applying
flask seed [--reset]                    # demo data, password "labbook-demo"; refuses in production
flask create-admin --email E --name N [--invite] [--force]
flask run
.venv/Scripts/python -m pytest                              # all tests
.venv/Scripts/python -m pytest tests/test_auth.py -k reset  # single test
.venv/Scripts/ruff check labbook tests
```

Run `npm run css` after adding Tailwind classes. The compiled `labbook/static/css/app.css` and `labbook/static/icons.svg` are committed outputs. To add an icon, add its Lucide name to `scripts/build-icons.mjs`, run `npm run icons`, then use `{{ icon("name") }}`. Emails go to `instance/outbox/*.eml` in development (`MAIL_BACKEND=outbox`) and to `labbook.services.mail.outbox()` in tests.

## Stack (overrides spec §7.1)

- Flask 3 app factory (`labbook/__init__.py`): SQLite pragmas on connect (WAL, `busy_timeout`, `foreign_keys`), blueprints `auth`, `main`, `catalogue`, `units`, `admin`, and a privacy re-consent gate in `before_request` (FR-D1).
- SQLAlchemy 2 typed models + Flask-Migrate (batch mode for SQLite). Flask-Login, `argon2-cffi`, Flask-WTF/WTForms (CSRF + validation), `markdown-it-py` with raw HTML disabled.
- Jinja2 + Tailwind v4 (npm `@tailwindcss/cli`, pinned) + vanilla JS (`static/js/shell.js`). HTMX gets added when a slice needs it.
- Spec's `npm run create-admin` / `npm run seed` are `flask create-admin` / `flask seed`, with the same refusal rules (§4.13).
- Later slices: jobs run via `flask jobs tick` (cron/Task Scheduler) or `POST /api/jobs/tick` with a secret header.

## Code conventions

- **All datetimes use `UTCDateTime`** (`models/types.py`), which rejects naive datetimes; use `labbook.timeutil.utcnow()`. Enums (`Role`, `UnitStatus`) are `StrEnum`s stored as strings with CHECK constraints.
- `labbook/services/` holds all state changes. Services call `audit.record(...)` but **never commit**; views commit. Raise `UserError` / `CatalogueError` for user-facing refusals.
- Rule defaults: `services/settings.py` (`DEFAULTS`, `RULES`) plus the `setting` table; `effective_rules(type)` applies per-type overrides.
- Auth: server-side `UserSession` rows keyed by a SHA-256 hash of the cookie token (7-day idle, 30-day absolute expiry), plus DB-backed rate limiting (`AuthAttempt`). Email tokens (`services/tokens.py`) are signed itsdangerous payloads; reset and invite tokens stop working once the password hash changes.
- Permissions: `@role_required(Role.X)` (`labbook/permissions.py`); Admin always passes `has_role`. The admin blueprint checks the role in `before_request`. Role-based nav is in `labbook/nav.py`; add items as pages ship.
- Templates: `base.html` (app shell: sidebar/drawer, theme toggle, toasts, confirm `<dialog>`) and `auth_layout.html`. Shared macros are in `templates/macros.html`; badges must keep icon + text + colour (§6.8). Macros are imported without context, so pass `request.args` explicitly. Destructive POST forms use `data-confirm="..."`. `form_page.html` renders simple WTForms pages.
- Tests: `tests/conftest.py` pushes an app context for direct DB access and uses `IsolatedClient`, which runs each request in its own app context and commits the test session before and after each request. Without it, `g`, the DB session and SQLite read snapshots leak between requests. After writes from another connection (e.g. threads), call `db.session.rollback()` before asserting. Fixtures: `make_user`, `login_as(role)`, `mailbox`, `link_from(msg, "/path/")`.
- SQLAlchemy emits `BEGIN` itself (pysqlite autocommit is off; see `_sqlite_begin` in `labbook/__init__.py`), so every read opens a transaction and a WAL snapshot that lasts until commit/rollback.

## Booking architecture (slice 2)

- `labbook/rules.py`: the pure rules engine (FR-B3, §4.6), with no Flask/DB imports. `check(BookingContext) -> list[Violation]`.
- `services/bookings.py`: `evaluate()` gathers facts (settings, the user's bookings, opening hours, free units) into a `BookingContext`. `create` / `reschedule` / `undo_cancel` run inside `services/db.write_transaction()` (BEGIN IMMEDIATE), re-evaluate inside the lock, then insert inside `begin_nested()` savepoints, falling through units on trigger `IntegrityError`. Live validation (`POST /bookings/validate`) calls the same `evaluate()`; there is no JS copy of the rules.
- `services/calendar.py`: opening hours per lab and date (term vs vacation hours, holidays). `services/availability.py`: busy intervals, `free_units()` ordering (preferred unit, then fewest bookings that day, then asset tag), and catalogue "free now". `services/timeline.py`: slot and segment data for the picker.
- Picker UI: `templates/bookings/_picker.html` (included by the equipment detail page and the edit page) plus `static/js/timeline.js`. The JS keeps the selection in sync with the Start/End selects, which are the no-JS/accessible fallback.
- Students never see other people's names or booking details: the timeline and calendar show "Booked", and other users' booking pages return 404.

**SQLite replacement for the §7.3 exclusion constraints:** booking writes run in `BEGIN IMMEDIATE` transactions, plus `BEFORE INSERT/UPDATE` triggers (`models/triggers.py`) on `booking` and `maintenance_block` that `RAISE(ABORT)` on overlap. `ACTIVE_STATUSES` is defined once in `models/booking.py` and the trigger SQL is generated from it; the slice 2 migration imports `CREATE_STATEMENTS`. If the active statuses ever change, add a migration that drops and recreates the triggers. `tests/test_migrations.py` checks that migrated and `create_all` databases have the same triggers.

**MVP slices:** (1) foundation, auth, admin setup, read-only catalogue → (2) booking core + timeline → (3) approvals, notifications, jobs, teacher bump → (4) lab desk, no-shows, maintenance, CSV/audit. Recurring series, waitlist, reports/charts, anonymisation and Docker are post-MVP.

## Architecture essentials (things that span multiple spec sections)

**Booking model.** Users book an *equipment type* + time range; the server assigns a free in-service *unit* (fewest bookings that day), or the user may pin a specific unit (§3.1). Labs have term hours vs. vacation hours chosen by the academic calendar (Terms, Holidays — §4.16).

**State machine (§5).** Statuses: `OFFERED, PENDING, CONFIRMED, CHECKED_OUT, COMPLETED, CANCELLED, REJECTED, EXPIRED, NO_SHOW, BUMPED, LAPSED`. Only the **active** set `OFFERED | PENDING | CONFIRMED | CHECKED_OUT` blocks a unit's slot — this set must stay identical across the DB constraint, the rules engine and availability queries.

**Double-booking prevention is in the database (§7.3, adapted for SQLite above).** It must hold under concurrent requests; don't replace it with app-level checks only. If every candidate unit fails, return "No units available".

**Rules engine (§7.4).** All booking rules (§4.3–4.8: durations, advance windows, student quotas, approval, grace periods, overrides, etc.) live in `labbook/rules.py`, which takes user, type, time range, settings and existing bookings and returns ok or typed violations. The server is authoritative. Rule defaults live in the `Setting` table; per-`EquipmentType` values override them (§4.6). Quotas apply to Students only and count Pending + Confirmed.

**Time handling (§7.5).** Store UTC `timestamptz`; display/validate in the lab's timezone (default `Asia/Kolkata`); quota weeks are ISO weeks in the lab timezone. Ranges are half-open `[start, end)`.

**Background jobs (§7.6).** `expirePending`, `detectNoShows`, `detectOverdue`, `sendReminders` (every minute), `dailyDigest` (07:00), `processWaitlist` (every minute *and* immediately when a slot frees), `anonymise` (monthly). Every job must be idempotent.

**Routes (§7.7).** Mutations are CSRF-protected form/HTMX POSTs. Every view uses `@role_required(...)` plus an ownership check per the permissions matrix (§2.1). State changes go through `labbook/services/` (which writes the audit log), not directly from views.

**Cross-cutting requirements.** Audit-log every override and admin change (§4.12); students see others' bookings only as anonymous "Booked" blocks; no PII in logs; anonymisation after retention period must leave report totals unchanged (§4.17, acceptance #17).

**UI (§6).** Mobile-first (360 px), light/dark via CSS-variable tokens in `static/src/input.css` read by Tailwind (token values in §6.2), single teal accent, status never conveyed by colour alone (one Jinja badge macro for §6.8), WCAG 2.1 AA. The booking timeline (§6.7) is a server-rendered grid driven by `timeline.js` (Pointer Events drag-to-select).

## Verifying work

Spec §9 lists 17 acceptance scenarios (concurrent last-unit booking, quotas, no-show suspension, teacher bump, recurring series, waitlist offers, holiday cancellation, anonymisation, …). Use them as the basis for pytest integration tests. Each slice ends with migrations applied, `flask seed` updated, tests passing, and the app running via `flask run`.
