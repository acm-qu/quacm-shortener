# QU ACM Link Shortener: System Design

## 1. Purpose

A small web service that gives the QU ACM Student Chapter short, branded links of the form `link.qu.acm.org/<link-name>`. At events, the team puts these links in slides so attendees can reach longer URLs (forms, repos, resources) quickly and professionally. Links can be created and changed by the team without redeploying anything.

## 2. Requirements

**Functional**

- Visiting `link.qu.acm.org/<link-name>` redirects the visitor to the stored target URL.
- Authorized team members can create, edit, disable, and delete links through an admin page.
- Each link has a custom, human-readable name (the slug).
- Unknown, disabled, or expired links show a branded error page instead of a raw error.
- Click counts are recorded per link.
- Each link has a QR code that can be downloaded and placed on a slide.

**Non-functional**

- Redirects must be fast (a single indexed database lookup).
- Only the team can create or edit links, so the domain can't be abused.
- Simple to deploy and maintain by student volunteers.

**Constraints (from the brief and the hosting team)**

- Python and/or Node.js, with any framework.
- Database: **PostgreSQL 9.6**. No features from PostgreSQL 10 or later are used.
- Hosting: **cPanel**. Docker and a reverse proxy are not available, so the app runs through cPanel's own Python or Node.js app tool.
- This design is sent for review before implementation begins.

**Out of scope (v1)**

- Public self-service link creation, per-visitor analytics, custom domains per link, link previews.

## 3. Technology Choices

| Layer | Choice | Reason |
| --- | --- | --- |
| Language / framework | Python 3 with Flask | Flask is a WSGI app, which is what cPanel's Python tool runs; small and easy to read |
| App runner | cPanel "Setup Python App" | Provided by the host, so no Docker or proxy is needed |
| Templates | Jinja2 | Server-rendered admin and error pages, with automatic escaping |
| DB driver | `psycopg2` | Mature and compatible with PostgreSQL 9.6 |
| Database | PostgreSQL 9.6.24, created in cPanel | Required by the brief |
| Password hashing | `bcrypt` | Standard, well-supported |
| Security add-ons | Flask-WTF (CSRF), Flask-Limiter (rate limits) | Cover common web attacks with little code |
| QR codes | `qrcode` library | Generated server-side as PNG or SVG |
| HTTPS | cPanel AutoSSL (to be confirmed) | Provided by the host |

If cPanel only offers Node.js apps, the equivalent stack is Express with `pg`; the architecture below does not depend on the language. Which tool is available must be confirmed with the hosting team (see section 12).

## 4. Architecture

```
  Visitor / Attendee                Team member (admin)
        |                                  |
        |  GET /<slug>                     |  GET /admin ...
        v                                  v
  +---------------------------------------------------+
  |   cPanel web server (HTTPS) - link.qu.acm.org     |
  |   passes requests to the Python app               |
  +---------------------------------------------------+
                          |
                          v
  +---------------------------------------------------+
  |   Web app (Flask)                                 |
  |   - Public: redirect + branded error pages        |
  |   - Admin: login, link CRUD, QR codes             |
  +---------------------------------------------------+
                          |
                          v
  +---------------------------------------------------+
  |   PostgreSQL 9.6 (created in cPanel)              |
  |   tables: links, admins                           |
  +---------------------------------------------------+
```

One application handles both the public redirect route and the admin pages. cPanel's web server provides HTTPS and passes requests to the app, so no separate proxy is needed. There is no separate frontend and no cache layer in v1; a single indexed lookup is fast enough for event traffic.

## 5. Data Model (PostgreSQL 9.6 compatible)

```sql
CREATE TABLE links (
    id           SERIAL PRIMARY KEY,
    slug         VARCHAR(64)  NOT NULL,
    target_url   TEXT         NOT NULL,
    created_by   VARCHAR(100) NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    click_count  BIGINT       NOT NULL DEFAULT 0
);

-- Case-insensitive unique slugs, also serves as the lookup index
CREATE UNIQUE INDEX links_slug_lower_idx ON links (lower(slug));

CREATE TABLE admins (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(50) NOT NULL UNIQUE,
    password_hash TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Notes for 9.6 compatibility: `SERIAL` is used instead of identity columns, there are no generated columns or declarative partitioning, and no extensions are required.

## 6. Endpoints

**Public**

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/<slug>` | Looks up the active, unexpired link, increments `click_count`, and returns a **302** redirect to `target_url`. Otherwise returns a branded 404 page. |
| GET | `/` | Simple landing page for the ACM chapter. |
| GET | `/healthz` | Health check for monitoring. |

**Admin** (login required)

| Method | Path | Behavior |
| --- | --- | --- |
| GET / POST | `/admin/login` | Login form and session creation. |
| POST | `/admin/logout` | Ends the session. |
| GET | `/admin` | Lists all links with target, clicks, status, and expiry. |
| POST | `/admin/links` | Creates a link. |
| POST | `/admin/links/<id>` | Edits a link (target, expiry, active flag). |
| POST | `/admin/links/<id>/delete` | Deletes a link. |
| GET | `/admin/links/<id>/qr` | Returns the link's QR code as PNG or SVG. |

The redirect uses a single statement so the lookup and the counter update happen together:

```sql
UPDATE links
   SET click_count = click_count + 1
 WHERE lower(slug) = lower(%s)
   AND is_active
   AND (expires_at IS NULL OR expires_at > now())
RETURNING target_url;
```

## 7. Key Design Decisions

- **302, not 301.** A temporary redirect means browsers don't cache the destination, so a link can be edited later and the change takes effect immediately.
- **Reserved slugs.** Names such as `admin`, `api`, `login`, `static`, and `healthz` are rejected so they can't collide with real routes.
- **Slug rules.** Letters, digits, hyphens, and underscores only, 1 to 64 characters, matched case-insensitively.
- **Slugs are not hashed.** A slug is a public, readable name, not a secret, and the app needs the real value to look it up. Only admin passwords are hashed. A link that must not be guessable gets a random slug.
- **URL validation.** Targets must start with `http://` or `https://`. Schemes like `javascript:` and `data:` are rejected, and links pointing back to `link.qu.acm.org` are rejected to prevent redirect loops.
- **Counting clicks.** A single running counter per link; no personal data about visitors is stored.
- **Branding.** The landing, error, and admin pages use the QU ACM logo (white lettering with cyan circuit artwork, supplied by the team) on a dark background, with cyan (about `#00F0F0`) as the accent color. The logo has a transparent background and white text, so it must not be placed on a light background.

## 8. Security and OWASP Top 10 Coverage

- Admin pages require login. Passwords are stored as bcrypt hashes, never in plain text, with a minimum length of 12 characters.
- Sessions use signed, HTTP-only, secure, SameSite cookies with a short expiry.
- Forms are protected with CSRF tokens.
- All database queries are parameterized, and templates escape output automatically.
- Login attempts are rate-limited to slow down password guessing.
- All traffic is served over HTTPS; HTTP is redirected to HTTPS using cPanel's force-HTTPS setting.
- Security headers are sent on every response: Content-Security-Policy, Strict-Transport-Security, X-Content-Type-Options, X-Frame-Options, and Referrer-Policy.
- Debug mode is off in production and errors show a generic page.
- Secrets (database password, session key) are set as environment variables in the cPanel app settings, not stored in the code repository.
- Dependencies are listed with exact versions in `requirements.txt` and checked with `pip-audit`.

**OWASP Top 10 (2021) coverage**

| Risk | How the design handles it |
| --- | --- |
| A01 Broken access control | Every `/admin` route checks the login session on each request. Admin accounts are created manually by a team owner. |
| A02 Cryptographic failures | HTTPS only, bcrypt password hashes, secure cookies. No other sensitive data is stored. |
| A03 Injection | Parameterized SQL queries, auto-escaped templates, strict URL and slug validation. |
| A04 Insecure design | Reserved slugs, URL scheme allow-list, loop prevention, rate limits. Only logged-in admins can create redirects, which limits open-redirect abuse. |
| A05 Security misconfiguration | Debug mode off, security headers set, secrets kept in environment variables, no default accounts. |
| A06 Vulnerable and outdated components | Dependencies are pinned and reviewed each term. **PostgreSQL 9.6 has had no security patches since it reached end of life in November 2021.** This is an accepted risk because the version is required. It is reduced by keeping remote database access off, giving the app a database user with access to this database only, and taking regular backups. |
| A07 Identification and authentication failures | bcrypt, minimum password length, login rate limit, session expiry, logout. |
| A08 Software and data integrity failures | Pinned dependencies, deployment only from the team's own repository, no third-party plugins loaded at runtime. |
| A09 Security logging and monitoring failures | Logins (successful and failed) and every link create, edit, and delete are logged with the admin's name and time. Logs are reviewed after each event. |
| A10 Server-side request forgery | Not applicable: the server never fetches a user-supplied URL. It only redirects the visitor's browser. |

## 9. Deployment

- **Hosting:** the team's cPanel hosting. The developer works through a member account.
- **Subdomain:** `link.qu.acm.org` is created in cPanel and points at the app. Any DNS change for `qu.acm.org` is made by whoever manages that domain.
- **App:** created with cPanel's Setup Python App (or Setup Node.js App if that is what the host offers). The app lives outside `public_html`, and its environment variables are set in that tool.
- **Database:** a PostgreSQL 9.6 database and a dedicated user, created in cPanel's PostgreSQL Databases tool. Remote access stays off. The schema is applied from a `schema.sql` file using the tool the host provides, such as phpPgAdmin.
- **HTTPS:** a certificate from cPanel AutoSSL, plus the force-HTTPS redirect.
- **Updates:** upload the new code (cPanel Git Version Control or File Manager), install dependencies from `requirements.txt`, and restart the app from the Python app tool.
- **Backups:** cPanel backups plus a periodic export of the database.
- **Development:** the developer runs the app locally against a PostgreSQL 9.6 database. Docker may be used on the developer's own machine for that, but it is not part of production.

## 10. Testing Plan

- **Hosting:** the team's cPanel hosting. The developer works through a member account.
- **Subdomain:** `link.qu.acm.org` does not exist yet. The team manages the `qu.acm.org` DNS through cPanel, so it is created there and pointed at the app.
- **App:** created with cPanel's Setup Python App (or Setup Node.js App if that is what the host offers). The app lives outside `public_html`, and its environment variables are set in that tool.
- **Dependencies:** installed from `requirements.txt` using the Python app tool's own install step, so SSH is not needed. SSH is available, but heavy use can trip the host's limits and send an alert email to the team, so it is kept for emergencies only.
- **Resource limits:** the host enforces limits on CPU, memory, and processes, so the app runs as one lightweight worker and avoids heavy libraries.
- **Database:** a PostgreSQL 9.6.24 database and a dedicated user, created in cPanel's PostgreSQL Databases tool. Remote access stays off. The schema is applied from a `schema.sql` file using the tool the host provides, such as phpPgAdmin.
- **HTTPS:** a certificate from cPanel AutoSSL, plus the force-HTTPS redirect.
- **Updates:** upload the new code (cPanel Git Version Control or File Manager), install dependencies from `requirements.txt`, and restart the app from the Python app tool.
- **Backups:** cPanel backups plus a periodic export of the database.
- **Development:** the developer runs the app locally against a PostgreSQL 9.6 database. Docker may be used on the developer's own machine for that, but it is not part of production.

## 11. Implementation Plan

1. Local project setup, PostgreSQL 9.6 for development, and the schema script.
2. Public redirect route and branded error page.
3. Admin login, sessions, CSRF protection, and rate limiting.
4. Admin link management pages.
5. QR code generation, security headers, and logging.
6. Tests, then deployment to cPanel: subdomain, Python app, database, and HTTPS.
