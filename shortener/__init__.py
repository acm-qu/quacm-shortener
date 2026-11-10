"""Application factory for the QU ACM Student Chapter link shortener.

Public (Stage 1):
  - /            landing page
  - /healthz     health check
  - /<slug>      the short-link redirect
  - branded 404

Admin (Stages 2-4):
  - login/logout with CSRF + rate limiting, sessions (2 h / 30 min idle)
  - link create, edit, disable/enable, delete
  - QR code images (SVG)
  - rotating-file audit log (logs/app.log)
  - security headers on every response
"""
import os
from datetime import timedelta
from time import time

from flask import Flask, render_template, request, session
from flask_limiter.errors import RateLimitExceeded
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from .admin import bp as admin_bp
from .db import init_db
from .extensions import limiter
from .logging_setup import one_line, setup_logging
from .public import bp as public_bp

# The placeholder value shipped in .env.example. Production refuses to
# start with this, so a template value can never reach the real site.
_EXAMPLE_SECRET = "paste-a-long-random-string-here"

# Session policy: signed out after 2 hours total, or 30 minutes idle.
SESSION_ABSOLUTE_LIFETIME = timedelta(hours=2)
SESSION_IDLE_TIMEOUT = timedelta(minutes=30)

csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)

    # "Production" means: running on cPanel behind the host's web server.
    # Set TRUST_PROXY=1 ONLY there (cPanel Environment Variables, Stage 5);
    # leave it unset locally.
    production = os.environ.get("TRUST_PROXY") == "1"

    # Wire up the rotating log file FIRST so early messages land in it.
    setup_logging(app)

    # The secret key comes from the environment only - never hard-coded.
    secret_key = os.environ.get("SECRET_KEY", "")
    if production and (not secret_key or secret_key == _EXAMPLE_SECRET):
        raise RuntimeError(
            "Refusing to start in production: SECRET_KEY is missing or is "
            "still the example value from .env.example. Set a long random "
            "SECRET_KEY in the cPanel Environment Variables screen."
        )
    app.config["SECRET_KEY"] = secret_key or None
    if not app.config["SECRET_KEY"]:
        app.logger.warning(
            "SECRET_KEY is not set - set it in .env or admin login sessions "
            "cannot work."
        )

    # Cookie + session policy (OWASP: HttpOnly, SameSite, short expiry).
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,     # JavaScript cannot read the cookie
        SESSION_COOKIE_SAMESITE="Lax",    # not sent on cross-site form posts
        # HTTPS-only cookie on cPanel; off locally (http://127.0.0.1:5000).
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=SESSION_ABSOLUTE_LIFETIME,
    )

    if production:
        # On cPanel, Apache sits in front of the app and passes the real
        # visitor IP in X-Forwarded-For. Trust exactly one proxy hop so
        # rate limiting keys on the visitor, not on 127.0.0.1.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

    csrf.init_app(app)   # CSRF token required on every POST form
    limiter.init_app(app)

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)

    # Create tables + first admin if needed (safe to run at every start;
    # Passenger restarts the app often, so this must never crash on rerun).
    init_db()

    @app.before_request
    def enforce_session_timeouts():
        """Sign out after 2 h total OR 30 minutes without any request."""
        if "admin_id" not in session:
            return
        now = time()
        created_at = session.get("_created_at")
        last_active = session.get("_last_active") or created_at
        expired = (
            created_at is None
            or now - created_at > SESSION_ABSOLUTE_LIFETIME.total_seconds()
            or now - last_active > SESSION_IDLE_TIMEOUT.total_seconds()
        )
        if expired:
            session.clear()
        else:
            session["_last_active"] = now  # sliding "last activity" mark

    @app.after_request
    def security_headers(response):
        """OWASP hardening headers on EVERY response (pages, QRs, errors)."""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'; "
            "base-uri 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if production:
            # Only meaningful on HTTPS - cPanel serves https, local does not.
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains")
        return response

    @app.errorhandler(404)
    def not_found(error):
        return render_template("404.html"), 404

    @app.errorhandler(RateLimitExceeded)
    def too_many_requests(error):
        app.logger.warning("Rate limit exceeded: path=%s ip=%s",
                           request.path, request.remote_addr)
        return render_template("429.html"), 429

    @app.errorhandler(CSRFError)
    def csrf_failure(error):
        return render_template("400.html"), 400

    return app
