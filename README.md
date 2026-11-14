# LabBook

Book shared lab equipment within one department. Flask + SQLite + Tailwind. The product spec is in `spec.md`.

## Setup (Windows, Git Bash)

```bash
python -m venv .venv                       # Python 3.12+
.venv/Scripts/pip install -e ".[dev]"
npm install                                # Tailwind CLI + Lucide icons (build-time only)
npm run build                              # icons sprite + labbook/static/css/app.css
cp .env.example .env                       # then edit SECRET_KEY etc.
export PATH="$PWD/.venv/Scripts:$PATH"     # or activate the venv
flask db upgrade
```

Then either load demo data:

```bash
flask seed            # demo labs, equipment and users; password "labbook-demo"
flask seed --reset    # wipe and reseed
```

or start clean with a real admin:

```bash
flask create-admin --email you@example.edu --name "Your Name"            # prompts for a password
flask create-admin --email you@example.edu --name "Your Name" --invite   # emails a set-password link
```

Run it:

```bash
flask run             # http://127.0.0.1:5000
npm run css:watch     # in another terminal while editing templates
```

With `MAIL_BACKEND=outbox` (the default in development), emails such as verification and reset links are written to `instance/outbox/*.eml` instead of being sent.

## Scheduled jobs

Reminders, pending-request expiry and the 07:00 approvals digest run from a job tick. Every job is safe to
run repeatedly.

```bash
flask jobs tick          # run due jobs once; schedule every minute (cron / Windows Task Scheduler)
flask jobs run           # development: tick every 60 s until Ctrl+C
```

Or set `JOBS_SECRET` and have cron call `POST /api/jobs/tick` with the header `X-Jobs-Secret: <secret>`.

## Deploying to Render

`render.yaml` is a Render Blueprint: a web service (gunicorn, SQLite on a 1 GB persistent disk at `/var/data`) and a cron job that calls `POST /api/jobs/tick` every minute.

1. On render.com: **New → Blueprint**, connect the GitHub repo and pick this branch.
2. Fill in the prompted values: SMTP settings and `MAIL_FROM` (verification, invite and reset emails need real email), and for the cron job `LABBOOK_URL` = the web service URL, e.g. `https://labbook.onrender.com`. `SECRET_KEY` and `JOBS_SECRET` are generated.
3. After the first deploy, open the web service's **Shell** tab and run `flask create-admin --email you@college.edu --name "Your Name"`.
4. Sign in, then set the allowed email domains under Admin → Settings.

Notes: the disk needs a paid instance (Starter) and limits the service to one instance, which suits SQLite. Migrations run at start (`flask db upgrade`) because disks are not mounted during build. Render snapshots disks daily; for your own copies run `sqlite3 /var/data/labbook.db ".backup /var/data/backup.db"` in the Shell and download it. If you add a custom domain, set `BASE_URL` to it.

## Tests and lint

```bash
.venv/Scripts/python -m pytest                       # all tests
.venv/Scripts/python -m pytest tests/test_auth.py -k reset   # one test
.venv/Scripts/ruff check labbook tests
```

## Schema changes

```bash
flask db migrate -m "describe the change"   # review the generated file in migrations/versions/
flask db upgrade
```
