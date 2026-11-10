"""Public routes: landing page, health check and the /<slug> redirect."""
import re

from flask import Blueprint, abort, jsonify, redirect, render_template

from .db import get_conn

bp = Blueprint("public", __name__)

# Slugs: letters, digits, hyphens, underscores, 1-64 characters.
# Case-insensitivity is handled in SQL with lower(slug).
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RESERVED_SLUGS = {"admin", "api", "login", "logout", "static", "healthz"}

# One single statement does the whole redirect job:
#  - it only matches rows that are active and not expired,
#  - it bumps the click counter,
#  - and it hands back the destination URL (RETURNING).
# If nothing comes back, the link is unknown/disabled/expired -> 404.
REDIRECT_SQL = """
    UPDATE links
       SET click_count = click_count + 1
     WHERE lower(slug) = lower(%s)
       AND is_active
       AND (expires_at IS NULL OR expires_at > now())
    RETURNING target_url
"""


@bp.route("/")
def landing():
    return render_template("landing.html")


@bp.route("/healthz")
def healthz():
    """Simple health check the host / monitoring can ping."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return jsonify(status="ok")
    except Exception:
        return jsonify(status="error"), 503


@bp.route("/<slug>")
def shortlink(slug):
    # Reject anything that is not a valid slug shape (dots, spaces, ...)
    # and reserved names - visitors get the branded 404 page.
    if not SLUG_RE.match(slug) or slug.lower() in RESERVED_SLUGS:
        abort(404)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(REDIRECT_SQL, (slug,))
            row = cur.fetchone()

    if row is None:
        abort(404)

    target_url = row[0]
    # Belt and braces: stored targets must be http(s). They were validated
    # when the link was created (Stage 3), but we never trust the DB blindly.
    if not (target_url.startswith("http://") or target_url.startswith("https://")):
        abort(404)

    return redirect(target_url, code=302)
