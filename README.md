# QU ACM Link Shortener

A self-hosted short-link service for the Qatar University ACM Student Chapter,
planned to run at `link.qu.acm.org/<slug>`.

Built as a deliberately small, dependency-light Flask application for shared
cPanel hosting: **Python 3.9**, **PostgreSQL 9.6**, **Passenger (WSGI)**, no
Docker, no reverse-proxy config in the repo, and no packages that need
compiling.

## Features

- Short links of the form `https://link.qu.acm.org/<slug>` → 302 redirect to
  the target URL, with a click counter.
- Case-insensitive slugs; disabled or expired links serve a branded 404.
- Admin area with login (bcrypt, rate-limited, CSRF-protected), a dashboard,
  and full link management: create, edit, enable/disable, delete.
- Optional slugs (random 6-character names generated from a look-alike-safe
  alphabet) and optional expiry timestamps entered in Qatar time (UTC+3).
- SVG QR codes for every short link (no Pillow required).
- Security headers on every response, including a strict Content-Security-
  Policy that forbids all inline scripts and styles.
- Rotating audit log (`logs/app.log`) of logins and every link change — never
  logging passwords.
- Automatic schema creation and first-admin bootstrap on first start (the
  host has no SSH access; the app must be able to set itself up).

## Tech stack

| Layer      | Choice                                              |
|------------|-----------------------------------------------------|
| Language   | Python 3.9 (must match; see [Constraints](#design-constraints--assumptions)) |
| Web        | Flask 2.3.3 + Jinja2 templates, WSGI (Passenger)    |
| Database   | PostgreSQL 9.6 via `psycopg2-binary` (threaded pool, 1–4 connections) |
| Auth       | `bcrypt` (12 rounds), Flask-WTF (CSRF), Flask-Limiter (in-memory) |
| QR codes   | `qrcode` 7.4.2 with its built-in SVG writer (`pypng`) |
| Styling    | Single hand-written CSS file; dark theme with cyan `#00F0F0` accent |
| Frontend JS| One small file (`static/js/app.js`) for the Copy button |

All pinned exact versions live in [`requirements.txt`](requirements.txt).
Every package installs from a prebuilt wheel or is pure Python — there is no
compiler and no system library requirement on the host.

## How it works

```
Visitor  ──►  GET /<slug>  ──►  one SQL statement:
                                UPDATE links SET click_count = click_count + 1
                                 WHERE lower(slug) = lower(%s)
                                   AND is_active
                                   AND (expires_at IS NULL OR expires_at > now())
                                RETURNING target_url
             row found   → 302 redirect to target_url
             no row      → branded 404 page
```

- The redirect is **302 (temporary) on purpose**: browsers must not cache it,
  so a link can be retargeted and take effect immediately.
- Slug matching is case-insensitive; `/ACM-TEST` and `/acm-test` are the same
  link.
- A belt-and-braces check re-verifies the stored target starts with
  `http(s)://` before redirecting, even though every stored URL was validated
  at creation time.

The admin area (everything under `/admin`) is a Flask blueprint:
login → dashboard → create/edit/toggle/delete/QR, all requiring login, with a
CSRF token on every form and all writes going through parameterized SQL.

## Project structure

```
quacm-shortener/
├── passenger_wsgi.py          cPanel/Passenger entry point (exposes `application`)
├── run_local.py               local dev entry point (debug mode, loads .env)
├── requirements.txt           exact pinned package versions (Python 3.9)
├── .env.example               template for local secrets (never upload .env)
├── logs/                      audit log (auto-created; .htaccess blocks web access)
└── shortener/
    ├── __init__.py            create_app(): config, sessions, CSRF, security
    │                          headers, error pages, rate-limit logging
    ├── db.py                  connection pool, CREATE TABLE IF NOT EXISTS
    │                          schema, first-admin bootstrap
    ├── public.py              landing page, /healthz, /<slug> redirect,
    │                          slug constants (SLUG_RE, RESERVED_SLUGS)
    ├── admin.py               login/logout, dashboard, link CRUD, QR route,
    │                          validation helpers
    ├── extensions.py          shared Flask-Limiter instance (in-memory)
    ├── logging_setup.py       rotating file handler + one_line() sanitiser
    ├── templates/             base, landing, login, dashboard, link_form,
    │                          delete, 400, 404, 429
    └── static/
        ├── style.css          the whole theme (no inline styles anywhere)
        ├── js/app.js          CSP-safe Copy button (event delegation)
        └── logo.png           transparent PNG with white lettering —
                               only ever shown on the dark background
```

## Routes

| Method    | Path                          | Auth           | Purpose |
|-----------|-------------------------------|----------------|---------|
| GET       | `/`                           | public         | Landing page |
| GET       | `/healthz`                    | public         | Health check (`{"status":"ok"}`, 503 if DB unreachable) |
| GET       | `/<slug>`                     | public         | The redirect (or branded 404) |
| GET/POST  | `/admin/login`                | public         | Login form; **POST is rate-limited** (5/min and 30/hour per IP) |
| POST      | `/admin/logout`               | login required | Sign out (form, never a link) |
| GET       | `/admin`                      | login required | Dashboard: full short URL + Copy, status, click count, per-row actions |
| GET/POST  | `/admin/links/new`            | login required | Create a link |
| GET/POST  | `/admin/links/<id>/edit`      | login required | Edit target/expiry/active; **slug is read-only** |
| POST      | `/admin/links/<id>/toggle`    | login required | Enable ↔ disable (CSRF token required) |
| GET/POST  | `/admin/links/<id>/delete`    | login required | Confirmation page, then delete (frees the slug) |
| GET       | `/admin/links/<id>/qr`        | login required | SVG QR code; `?download=1` forces a download |

Unknown ids under `/admin/links/...` return the branded 404.

Admins log in at /admin (or use the Admin login link on the landing page).

## Database

PostgreSQL 9.6-compatible by design (see constraints below). Two tables,
created with `CREATE TABLE IF NOT EXISTS` at every app start:

```sql
CREATE TABLE IF NOT EXISTS links (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(64) NOT NULL,
    target_url TEXT NOT NULL,
    created_by VARCHAR(100) NOT NULL,      -- username string, no FK
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,                -- UTC
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    click_count BIGINT NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS links_slug_lower_idx ON links (lower(slug));

CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,           -- bcrypt, 12 rounds
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- The app creates the schema itself; there are **no migrations**. The schema
  must not be changed after first deployment.
- **First-admin bootstrap:** if the `admins` table is empty at startup, an
  admin is created from `ADMIN_USERNAME` / `ADMIN_PASSWORD` (skipped with a
  logged warning if the password is shorter than 12 characters). Once one
  admin exists, the variables are ignored — there is no password-change UI.
- Connections come from a `ThreadedConnectionPool` (1–4) and are always
  committed or rolled back by the `get_conn()` context manager.
- **All** SQL is parameterized. If `DATABASE_URL` is missing the app refuses
  to start with a helpful error.

### Slug rules

- Pattern: `^[A-Za-z0-9_-]{1,64}$`, compared case-insensitively (unique index
  on `lower(slug)`; an explicit pre-check plus a caught `UniqueViolation`
  covers simultaneous submissions).
- Reserved (cannot be shortened): `admin`, `api`, `login`, `logout`, `static`,
  `healthz`.
- Random slugs: 6 characters from `23456789abcdefghjkmnpqrstuvwxyz`
  (look-alikes `0 o 1 l i` excluded), generated with `secrets.choice`,
  retried on collision.

### Target-URL rules (create and edit)

- Maximum 2048 characters; must start with `http://` or `https://` —
  `javascript:`, `data:`, `file:` and scheme-less input (`example.com`) are
  rejected with a friendly message asking for the full address.
- Must have a host; no spaces or control characters; no `user:pass@` info.
- A URL pointing back at this site's own host (`link.qu.acm.org`, the
  `BASE_URL` host, or the current request host) is rejected to prevent
  redirect loops. Nothing is ever auto-prefixed.

### Expiry rules

- Optional. Entered in **Qatar time (fixed UTC+3, no daylight saving)** via a
  `datetime-local` input, converted to UTC and stored in the `TIMESTAMPTZ`
  column. Must be in the future when set.

## Configuration (environment variables)

The app reads **all** configuration from environment variables — no settings
are hard-coded. Locally they come from a `.env` file (copy `.env.example`);
on cPanel they are set in the *Setup Python App → Environment Variables*
screen.

| Name            | Required | Default | Purpose |
|-----------------|----------|---------|---------|
| `DATABASE_URL`  | yes      | — (app refuses to start) | `postgresql://USER:PASSWORD@HOST:PORT/DBNAME` |
| `SECRET_KEY`    | yes in production | `None` (login broken, warning logged) | Signs session cookies. In production (`TRUST_PROXY=1`) startup **fails** if missing or still the `.env.example` placeholder |
| `TRUST_PROXY`   | production only | off | Set to `1` **only on cPanel**: enables ProxyFix (one hop, `X-Forwarded-For`), HTTPS-only session cookies, HSTS, and the strict `SECRET_KEY` check. Never set locally |
| `BASE_URL`      | no       | current request host | Public origin used to build full short URLs on the dashboard and inside QR codes, e.g. `https://link.qu.acm.org` |
| `ADMIN_USERNAME`| first run only | — | Username for the auto-created first admin (ignored once any admin exists) |
| `ADMIN_PASSWORD`| first run only | — | Its password; must be 12+ characters or bootstrap is skipped |

## Getting started (local development)

Prerequisites: **Python 3.9** and **PostgreSQL 9.6** (developed and tested
against 9.6.24 on Windows). Adjust the `psql` path for your install.

```powershell
# 1. Create the database (app creates the tables itself)
& "C:\Program Files\PostgreSQL\9.6\bin\psql.exe" -U postgres -c "CREATE DATABASE quacm;"

# 2. Project + virtual environment
cd quacm-shortener
py -3.9 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

# 3. Configuration
Copy-Item .env.example .env
notepad .env    # fill in DATABASE_URL, SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD
```

Run (debug mode, auto-reload, loads `.env`):

```powershell
.\.venv\Scripts\Activate.ps1
python run_local.py
```

Then open:

- `http://127.0.0.1:5000/` — landing page
- `http://127.0.0.1:5000/healthz` — health check
- `http://127.0.0.1:5000/nope` — branded 404
- `http://127.0.0.1:5000/admin` — login, then the dashboard

Seed a test link if you like:

```powershell
$env:PGPASSWORD = "YOUR-PG-PASSWORD"
& "C:\Program Files\PostgreSQL\9.6\bin\psql.exe" -U postgres -d quacm -c "INSERT INTO links (slug, target_url, created_by) VALUES ('acm-test', 'https://www.qu.edu', 'setup');"
# http://127.0.0.1:5000/acm-test  → 302 to https://www.qu.edu
```

> `TRUST_PROXY` must stay unset/0 locally — it is a production-only switch.

## Security model

What is implemented, and where:

- **Passwords:** bcrypt with 12 rounds. Unknown usernames still cost one
  bcrypt check against a dummy hash (`_DUMMY_HASH` in `admin.py`), so response
  timing cannot reveal which usernames exist. Login errors are always the
  same generic message.
- **Sessions:** signed cookies, `HttpOnly`, `SameSite=Lax`, `Secure` in
  production. Fresh empty session on login (fixation protection). Signed out
  after 2 hours absolutely or 30 minutes idle, enforced by a
  `before_request` hook.
- **CSRF:** `CSRFProtect` on every POST form; a CSRF failure renders a
  branded 400 page. CSRF validation happens before rate limiting.
- **Rate limiting:** Flask-Limiter, in-memory fixed-window storage, keyed on
  remote address (real visitor IP via ProxyFix in production). Applies to
  `POST /admin/login` only: 5/minute and 30/hour. Exceeding it renders a
  branded 429 page and logs a warning.
- **Security headers** (`after_request`, on every response including 404s and
  QR images):
  - `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'`
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains` — production only
  - Consequence for contributors: **no inline `<script>` or `style=` attributes
    and no inline event handlers** anywhere in templates; everything lives in
    the static files.
- **Logging hygiene:** user-supplied values pass through `one_line()` (newlines
  stripped) so a crafted username cannot forge log lines. Passwords,
  `SECRET_KEY`, and cookies are never logged.
- **Log file access:** `logs/.htaccess` (`Require all denied`) blocks web
  access on Apache-based hosts; `logs/` is also git-ignored.

### Audit log contents (`logs/app.log`)

Rotating file handler: 1 MB per file, 5 backups, UTF-8. If the folder cannot
be created/written, the app logs a warning and falls back to console logging
instead of crashing.

| Event | Fields logged |
|---|---|
| Login success / failure | username (as entered) + IP — never the password |
| Logout | username |
| Rate-limit hit | request path + IP |
| Link created / edited / enabled / disabled / deleted | link id, slug, admin username |

## QR codes

`GET /admin/links/<id>/qr` (login required) returns `image/svg+xml` encoding
the full short URL (`BASE_URL`-based), generated with
`qrcode.image.svg.SvgPathFillImage` — pure Python, no Pillow. Error
correction level **M**, border 4. `?download=1` adds
`Content-Disposition: attachment; filename="<slug>-qr.svg"`. The dashboard
rows and the edit page embed/preview the same route.

## Deployment (cPanel "Setup Python App")

Overview — the host provides the Python version, database, and Apache in
front of Passenger; nothing in this repo configures a web server.

1. In cPanel, create a **PostgreSQL database and user** (names get the
   cPanel account prefix), then create a **Python 3.9 app** (Passenger).
2. Build the upload zip with Python's `zipfile` (not `Compress-Archive`,
   which produces zips that misbehave on Linux), excluding
   `.venv/`, `.env`, `logs/`, `__pycache__/`, `*.pyc`, `.git/`, `deploy/`,
   and `run_local.py`:

   ```python
   # run from the quacm-shortener folder
   import os, zipfile
   ROOT = os.getcwd()
   SKIP_DIRS = {".venv", "logs", "__pycache__", ".git", "deploy"}
   with zipfile.ZipFile("deploy/quacm-shortener.zip", "w", zipfile.ZIP_DEFLATED) as zf:
       for dirpath, dirnames, filenames in os.walk(ROOT):
           dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
           for name in filenames:
               if name in {".env", "run_local.py"} or name.endswith((".pyc", ".pyo")):
                   continue
               full = os.path.join(dirpath, name)
               zf.write(full, os.path.relpath(full, ROOT).replace(os.sep, "/"))
   ```

3. Upload and extract the zip into the app's folder; `passenger_wsgi.py` must
   end up at the top level (it exposes `application` and puts its own folder
   on `sys.path`).
4. Enter the environment variables from the table above in the
   **Environment Variables** screen — including `TRUST_PROXY=1` and
   `BASE_URL=https://link.qu.acm.org`. **Never upload the `.env` file.**
5. Install `requirements.txt` via the app page, then Restart. The first page
   load creates the tables and (if set) the first admin.

Production differences to expect: login only works over **https** (Secure
cookie), rate limiting keys on the real visitor IP, and short links/QR codes
show the real domain once `BASE_URL` is set. After changing any environment
variable, click **Restart** on the app page.

## Manual test checklist

There are **no automated tests** in this repo (deliberate choice). After
changing anything, at minimum verify:

1. Login fails with a generic message on a wrong password; succeeds with
   correct credentials; failed and successful attempts appear in
   `logs/app.log` (username + IP, no password).
2. Create a link with a custom slug and open it; create one with a blank
   slug and check the generated 6-character name.
3. Duplicate slug (including different capitalisation), reserved slug
   (`admin`), `javascript:alert(1)`, `example.com`, and a self-hosted target
   are all rejected; a past expiry is rejected.
4. Edit the target and confirm the redirect changes immediately; the slug
   field cannot be changed.
5. Disable a link → public URL returns the branded 404; enable → works
   again. Delete → confirmation page → 404 → the slug can be created again.
6. The QR page returns an SVG that scans to the right URL; the QR route is
   blocked (login redirect) when logged out.
7. Response headers present (`curl.exe -I http://127.0.0.1:5000/`): CSP,
   `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy` — and **no** HSTS
   locally.
8. No red CSP errors in the browser console on any page; the Copy button
   works.
9. A POST without a CSRF token is rejected with the branded 400 page.
10. `logs/app.log` shows create/edit/enable/disable/delete events and no
    passwords.

## Design constraints & assumptions

These are project rules, not accidents — preserve them when contributing:

- **Python 3.9 only.** The code avoids 3.10+ syntax because cPanel offers 3.9
  for the app runtime, and the pins match.
- **PostgreSQL 9.6 compatibility.** `SERIAL` ids only — no `GENERATED ... AS
  IDENTITY`, no generated columns, no `gen_random_uuid()`. The schema is
  created with `IF NOT EXISTS` and must **not** be altered after deployment
  (there is no migration system).
- **No packages that need compiling** or system libraries; wheels or pure
  Python only; no Pillow (QR output is SVG).
- **No SSH access on the host.** Everything administrative happens through
  the app itself (self-bootstrapping schema/admin) or the cPanel UI.
- **Secrets only from environment variables** — never hard-coded, never
  committed. `.env` exists locally only.
- **Single-domain, single-role deployment:** one admin group, no public
  sign-ups, `created_by` stored as a plain username string (no foreign key).
- **CSP-safe frontend:** no inline styles/scripts/handlers; templates rely on
  `static/` files only.
- Expiry input is fixed **UTC+3** (Qatar has no daylight saving) by design.

## Limitations & known issues

- **No automated tests.** Correctness is maintained via the manual checklist
  above.
- **In-memory rate limiting:** counters reset whenever the app restarts and
  are per process. The deployment assumption is a single app worker
  (see the comment in `extensions.py`); with multiple Passenger processes
  each process keeps its own counters, so limits would be looser.
- **No pagination or search on the dashboard** — fine for hundreds of links,
  not thousands.
- **No password-change or password-reset UI.** Changing the admin password
  requires direct database access (`UPDATE admins ...` with a new bcrypt
  hash) or wiping the `admins` table and re-bootstrapping.
- **Slugs are immutable by design** (they appear on printed material);
  "renaming" means deleting and re-creating.
- **No analytics beyond a click counter** (no timestamps, IPs, or referrers
  for individual clicks).
- **SVG-only QR codes.** If a PNG were ever required, that would need Pillow
  or another rasteriser, which the no-compile constraint currently excludes.
- **Redirects are always 302,** never 301 — intentional (immediate
  retargeting), at the cost of no SEO/CDN caching benefit.
- **ProxyFix trusts exactly one hop** (`x_for=1`). If the hosting
  architecture ever changes (extra proxy layer), that number must be
  revisited, or rate limiting would key on the wrong client.
- **`created_by` is not a foreign key** and admin usernames are not checked
  against it; deleting an admin row does not affect links.
- **Log fallback is best-effort:** if `logs/` is unwritable the app keeps
  serving but only logs to stderr, whose visibility depends on the host's
  Passenger setup.

## Contributing notes

- Match the existing style: module docstrings explaining the *why*, small
  focused modules, flash messages with categories (`success`, `error`,
  `info`), and friendly error strings shown on the form with the user's
  input preserved.
- Every SQL statement is parameterized (`%s` placeholders, values as a
  tuple) — no string formatting into SQL, ever.
- Any new dependency must be a pinned exact version in `requirements.txt`
  and installable without a compiler.
- Any new table/column needs a 9.6-safe `CREATE TABLE IF NOT EXISTS` /
  `ALTER` story in `db.py`, respecting the no-schema-change-after-deployment
  rule.
- Verify with the manual checklist before opening a pull request; do not
  commit throwaway verification scripts, `.env`, `logs/`, or `deploy/`.

## License

No open-source license has been chosen yet; all rights reserved by the QU ACM
Student Chapter until one is added.
