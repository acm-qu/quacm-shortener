"""Admin area: login, logout, dashboard and link management.

Stage 2: /admin/login, /admin/logout, sessions, rate limiting.
Stage 3: create, edit, disable/enable, delete links (CSRF everywhere).
Stage 4: QR code image route + audit logging on every action.
"""
import io
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from time import time
from urllib.parse import urlsplit

import bcrypt
import qrcode
import qrcode.image.svg
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from psycopg2 import errors as pg_errors
from psycopg2.extras import DictCursor

from .db import get_conn
from .extensions import limiter
from .logging_setup import one_line
from .public import RESERVED_SLUGS, SLUG_RE

bp = Blueprint("admin", __name__)

# A throwaway bcrypt hash of a dummy password. When someone submits a
# username that does not exist, we still run ONE bcrypt check against
# this hash, so the request takes just as long as a real login attempt.
# Without it, response timing could reveal which usernames exist.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password-1234", bcrypt.gensalt(rounds=12))

# Qatar time is UTC+3 all year round (no daylight saving).
QATAR_TZ = timezone(timedelta(hours=3))

URL_MAX_LENGTH = 2048
# Random slugs: lowercase letters + digits WITHOUT look-alikes 0, o, 1, l, i.
SLUG_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
SLUG_LENGTH = 6

# Dashboard data. is_expired is computed by the database: TRUE once the
# expiry moment has passed (independent of the on/off switch).
DASHBOARD_SQL = """
    SELECT id, slug, target_url, click_count, is_active, created_at,
           expires_at,
           (expires_at IS NOT NULL AND expires_at <= now()) AS is_expired
      FROM links
     ORDER BY created_at DESC
"""


# ---------------------------------------------------------------- helpers

def full_short_url(slug):
    """The complete short link, e.g. https://link.qu.acm.org/slug.

    Uses the BASE_URL environment variable when set; otherwise the host
    the current request came in on (e.g. 127.0.0.1:5000 locally).
    """
    base = (os.environ.get("BASE_URL") or "").strip().rstrip("/")
    if base:
        return f"{base}/{slug}"
    return request.host_url.rstrip("/") + "/" + slug


def _self_hostnames():
    """Hosts that would send a visitor in a circle if used as a target."""
    hosts = {"link.qu.acm.org"}
    base = (os.environ.get("BASE_URL") or "").strip()
    if base:
        parsed = urlsplit(base if "://" in base else "http://" + base)
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    hosts.add(request.host.split(":")[0].lower())
    return hosts


def validate_slug(raw):
    """Optional slug field. Returns (slug, error).

    Empty string means "generate one". Otherwise: letters, digits,
    hyphens, underscores, 1-64 chars, reserved names rejected.
    """
    slug = (raw or "").strip()
    if not slug:
        return "", None
    if not SLUG_RE.match(slug):
        return None, ("The name may only use letters, numbers, hyphens "
                      "and underscores (1-64 characters).")
    if slug.lower() in RESERVED_SLUGS:
        return None, "That name is reserved - please pick another."
    return slug, None


def validate_target_url(raw):
    """Destination URL rules. Returns (url, error)."""
    url = (raw or "").strip()
    if not url:
        return None, "Please enter the destination URL."
    if len(url) > URL_MAX_LENGTH:
        return None, "That URL is too long (over 2048 characters)."
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        return None, "The URL may not contain spaces or control characters."
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        # Covers "example.com" (no scheme) and javascript:/data:/file: ...
        return None, ("Please enter the full address starting with "
                      "http:// or https:// - other schemes are not allowed.")
    if not parts.netloc or not parts.hostname:
        return None, "The URL needs a host, e.g. https://example.com/page."
    if parts.username or parts.password:
        return None, "The URL may not contain a username or password."
    if parts.hostname in _self_hostnames():
        return None, ("You cannot shorten a link back to this site "
                      "(it would loop).")
    return url, None


def parse_expires(raw):
    """Optional expiry, typed in Qatar time (UTC+3), stored as UTC.

    Returns (utc_datetime_or_None, error_or_None).
    """
    raw = (raw or "").strip()
    if not raw:
        return None, None
    try:
        chosen = datetime.fromisoformat(raw)
    except ValueError:
        return None, "Please pick a valid expiry date and time."
    chosen = chosen.replace(tzinfo=QATAR_TZ)
    if chosen <= datetime.now(QATAR_TZ):
        return None, "The expiry must be in the future."
    return chosen.astimezone(timezone.utc), None


def qatar_input_value(dt_utc):
    """UTC datetime -> value for <input type=datetime-local> (Qatar time)."""
    if not dt_utc:
        return ""
    return dt_utc.astimezone(QATAR_TZ).strftime("%Y-%m-%dT%H:%M")


def _slug_taken(slug):
    """True when lower(slug) already exists (same rule as the DB index)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM links WHERE lower(slug) = lower(%s)",
                        (slug,))
            return cur.fetchone() is not None


def generate_slug():
    """Random 6-character slug from safe characters, retried on collision."""
    while True:
        candidate = "".join(
            secrets.choice(SLUG_ALPHABET) for _ in range(SLUG_LENGTH))
        if not _slug_taken(candidate):
            return candidate


def _get_link(link_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(
                "SELECT id, slug, target_url, click_count, is_active, "
                "created_at, expires_at FROM links WHERE id = %s",
                (link_id,))
            return cur.fetchone()


def login_required(view):
    """Send logged-out visitors to the login page instead of the view.

    The ?next= value is validated by _safe_next() so it can never be
    used to bounce a browser to an outside site (no open redirects).
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "admin_id" not in session:
            next_path = request.full_path
            if next_path.endswith("?"):
                next_path = next_path[:-1]
            return redirect(url_for("admin.login", next=next_path))
        return view(*args, **kwargs)

    return wrapped


def _safe_next(target):
    """Accept ONLY relative paths that start with a single '/'.
    Rejects 'http://...', '//evil.com' and '/\\evil.com' tricks.
    """
    if (
        target
        and target.startswith("/")
        and not target.startswith("//")
        and not target.startswith("/\\")
    ):
        return target
    return url_for("admin.dashboard")


def _check_credentials(username, password):
    """Return the admin's id when username + password match, else None.

    Parameterized SQL only. Never logs the password or the hash.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, password_hash FROM admins WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()

    if row is None:
        # Unknown username: burn the same bcrypt time as a real check.
        bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
        return None

    admin_id, password_hash = row
    if bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii")):
        return admin_id
    return None


# ------------------------------------------------------------------ auth

@bp.route("/admin/login", methods=["GET", "POST"])
@limiter.limit("30 per hour", methods=["POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        admin_id = _check_credentials(username, password)
        if admin_id is not None:
            # Fresh, empty session first -> protects against session
            # fixation (an attacker can never pre-plant a session).
            session.clear()
            session["admin_id"] = admin_id
            session["admin_username"] = username
            session["_created_at"] = time()
            session["_last_active"] = time()
            session.permanent = True  # cookie expiry = 2 h (config)
            current_app.logger.info("Login success: user=%s ip=%s",
                                    one_line(username), request.remote_addr)
            return redirect(_safe_next(request.args.get("next")))

        # SAME generic message for wrong username AND wrong password.
        current_app.logger.warning("Login FAILED: user=%s ip=%s",
                                   one_line(username), request.remote_addr)
        flash("Invalid username or password.", "error")
    elif "admin_id" in session:
        return redirect(url_for("admin.dashboard"))

    return render_template("login.html")


@bp.route("/admin/logout", methods=["POST"])
@login_required
def logout():
    current_app.logger.info("Logout: user=%s",
                            one_line(session.get("admin_username", "")))
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("admin.login"))


# ------------------------------------------------------------- dashboard

@bp.route("/admin")
@login_required
def dashboard():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(DASHBOARD_SQL)
            links = [dict(row) for row in cur.fetchall()]
    for link in links:
        link["full_url"] = full_short_url(link["slug"])
    return render_template("dashboard.html", links=links)


# ------------------------------------------------------ link management

@bp.route("/admin/links/new", methods=["GET", "POST"])
@login_required
def link_new():
    if request.method == "POST":
        values = {
            "slug": (request.form.get("slug") or "").strip(),
            "target_url": (request.form.get("target_url") or "").strip(),
            "expires_at": (request.form.get("expires_at") or "").strip(),
        }
        errors = []

        slug, slug_error = validate_slug(values["slug"])
        if slug_error:
            errors.append(slug_error)
        if slug == "":  # blank -> random, retried on collision
            slug = generate_slug()

        target_url, url_error = validate_target_url(values["target_url"])
        if url_error:
            errors.append(url_error)

        expires_at, expires_error = parse_expires(values["expires_at"])
        if expires_error:
            errors.append(expires_error)

        if not errors and _slug_taken(slug):
            errors.append("That name is already taken.")

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("link_form.html", mode="create",
                                   link=None, values=values)

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO links (slug, target_url, created_by, "
                        "expires_at) VALUES (%s, %s, %s, %s) RETURNING id",
                        (slug, target_url, session["admin_username"],
                         expires_at),
                    )
                    new_id = cur.fetchone()[0]
        except pg_errors.UniqueViolation:
            # Two admins submitted the same name at the same moment.
            flash("That name is already taken.", "error")
            return render_template("link_form.html", mode="create",
                                   link=None, values=values)

        current_app.logger.info("Link created: id=%s slug=%s by=%s",
                                new_id, one_line(slug),
                                one_line(session["admin_username"]))
        flash(f"Short link /{slug} created.", "success")
        return redirect(url_for("admin.dashboard"))

    return render_template(
        "link_form.html", mode="create", link=None,
        values={"slug": "", "target_url": "", "expires_at": ""})


@bp.route("/admin/links/<int:link_id>/edit", methods=["GET", "POST"])
@login_required
def link_edit(link_id):
    link = _get_link(link_id)
    if link is None:
        abort(404)

    if request.method == "POST":
        values = {
            "slug": link["slug"],  # read-only: never taken from the form
            "target_url": (request.form.get("target_url") or "").strip(),
            "expires_at": (request.form.get("expires_at") or "").strip(),
        }
        errors = []

        target_url, url_error = validate_target_url(values["target_url"])
        if url_error:
            errors.append(url_error)

        expires_at, expires_error = parse_expires(values["expires_at"])
        if expires_error:
            errors.append(expires_error)

        is_active = request.form.get("is_active") == "1"

        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("link_form.html", mode="edit",
                                   link=link, values=values)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE links SET target_url = %s, expires_at = %s, "
                    "is_active = %s WHERE id = %s",
                    (target_url, expires_at, is_active, link_id))

        current_app.logger.info("Link edited: id=%s slug=%s by=%s",
                                link_id, one_line(link["slug"]),
                                one_line(session["admin_username"]))
        flash(f"Link /{link['slug']} updated.", "success")
        return redirect(url_for("admin.dashboard"))

    # GET: prefill the form from the database.
    values = {
        "slug": link["slug"],
        "target_url": link["target_url"],
        "expires_at": qatar_input_value(link["expires_at"]),
    }
    return render_template("link_form.html", mode="edit", link=link,
                           values=values)


@bp.route("/admin/links/<int:link_id>/toggle", methods=["POST"])
@login_required
def link_toggle(link_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE links SET is_active = NOT is_active "
                "WHERE id = %s RETURNING slug, is_active",
                (link_id,))
            row = cur.fetchone()

    if row is None:
        abort(404)
    slug, is_active = row
    current_app.logger.info("Link %s: id=%s slug=%s by=%s",
                            "enabled" if is_active else "disabled",
                            link_id, one_line(slug),
                            one_line(session["admin_username"]))
    if is_active:
        flash(f"/{slug} is now active.", "success")
    else:
        flash(f"/{slug} is now disabled - visitors see the 404 page.", "info")
    return redirect(url_for("admin.dashboard"))


@bp.route("/admin/links/<int:link_id>/delete", methods=["GET", "POST"])
@login_required
def link_delete(link_id):
    link = _get_link(link_id)
    if link is None:
        abort(404)

    if request.method == "POST":
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM links WHERE id = %s", (link_id,))
        current_app.logger.info("Link deleted: id=%s slug=%s by=%s",
                                link_id, one_line(link["slug"]),
                                one_line(session["admin_username"]))
        flash(f"Link /{link['slug']} deleted. The short link no longer "
              "works and the name is free again.", "success")
        return redirect(url_for("admin.dashboard"))

    # GET: confirmation page first.
    return render_template("delete.html", link=link,
                           full_url=full_short_url(link["slug"]))


@bp.route("/admin/links/<int:link_id>/qr")
@login_required
def link_qr(link_id):
    """SVG QR code of the full short link (qrcode's built-in SVG output).

    No Pillow involved. ?download=1 turns it into a file download.
    """
    link = _get_link(link_id)
    if link is None:
        abort(404)

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       border=4)
    qr.add_data(full_short_url(link["slug"]))
    qr.make(fit=True)
    # SvgPathFillImage = one combined path on a solid white background,
    # which scans reliably on our dark pages.
    svg_image = qr.make_image(image_factory=qrcode.image.svg.SvgPathFillImage)
    buffer = io.BytesIO()
    svg_image.save(buffer)

    response = Response(buffer.getvalue(), mimetype="image/svg+xml")
    if request.args.get("download"):
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{link["slug"]}-qr.svg"')
    return response
