"""Database access: a small psycopg2 connection pool, schema creation
and first-admin bootstrap.

PostgreSQL 9.6 compatible on purpose: SERIAL ids, no GENERATED ... AS
IDENTITY, no generated columns, no gen_random_uuid().
"""
import logging
import os
from contextlib import contextmanager

import bcrypt
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

# Everything the app needs is created here with IF NOT EXISTS, so it is
# safe to run at every startup (cPanel restarts the app on each deploy).
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS links (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(64) NOT NULL,
    target_url TEXT NOT NULL,
    created_by VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    click_count BIGINT NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS links_slug_lower_idx ON links (lower(slug));
CREATE TABLE IF NOT EXISTS admins (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_pool = None


def _get_pool():
    """Create the connection pool once, reuse it afterwards."""
    global _pool
    if _pool is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is not set. Locally: copy .env.example to .env "
                "and fill it in. On cPanel: set it in the Python app's "
                "Environment Variables screen (Stage 5)."
            )
        # Small pool on purpose - the host has resource limits.
        _pool = ThreadedConnectionPool(minconn=1, maxconn=4, dsn=database_url)
    return _pool


@contextmanager
def get_conn():
    """Lend out one pooled connection; commit on success, rollback on error.

    Used as:  with get_conn() as conn:  with conn.cursor() as cur: ...
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db():
    """Make sure the schema exists and the first admin account exists."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            _create_first_admin(cur)


def _create_first_admin(cur):
    """Create the first admin from ADMIN_USERNAME / ADMIN_PASSWORD
    environment variables, but only if no admin exists yet. On cPanel
    there is no SSH, so the app must be able to do this by itself.
    """
    cur.execute("SELECT 1 FROM admins LIMIT 1")
    if cur.fetchone() is not None:
        return  # an admin already exists - do nothing

    username = (os.environ.get("ADMIN_USERNAME") or "").strip()
    password = os.environ.get("ADMIN_PASSWORD") or ""
    if not username or not password:
        logger.warning(
            "No admin in the database and ADMIN_USERNAME/ADMIN_PASSWORD "
            "are not set; skipping admin bootstrap."
        )
        return
    if len(password) < 12:
        logger.warning(
            "ADMIN_PASSWORD is shorter than 12 characters; "
            "admin %r was NOT created.", username
        )
        return

    # bcrypt with 12 rounds, per the project security rules.
    password_hash = bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("ascii")
    cur.execute(
        "INSERT INTO admins (username, password_hash) VALUES (%s, %s)",
        (username, password_hash),
    )
    logger.info("Created first admin %r from environment variables.", username)
