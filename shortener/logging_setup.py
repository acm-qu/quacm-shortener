"""Rotating-file logging setup + helpers for safe log lines.

Stage 4: every important action lands in logs/app.log (1 MB per file,
5 backups). User-provided text is flattened to one line first, so a
crafted username can never forge extra log lines. Passwords, SECRET_KEY
and cookies are never logged.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_FILE = "app.log"
MAX_BYTES = 1_000_000  # 1 MB
BACKUP_COUNT = 5


def one_line(value):
    """Flatten user input to a single log line (strip newlines)."""
    return (value or "").replace("\r", " ").replace("\n", " ")


def setup_logging(app):
    """Attach a rotating file handler to the app's logger.

    If the logs folder cannot be created or written (some hosts have
    strict permissions), we keep running with console logging only
    instead of crashing the site.
    """
    # shortener/logging_setup.py -> project root is one level up.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logs_dir = os.path.join(project_root, "logs")

    try:
        os.makedirs(logs_dir, exist_ok=True)  # create the folder if missing
        handler = RotatingFileHandler(
            os.path.join(logs_dir, LOG_FILE),
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
    except OSError as exc:
        # Flask still logs to the console (Passenger captures that in the
        # cPanel error log), so the app keeps working without the file.
        app.logger.setLevel(logging.INFO)
        app.logger.warning(
            "Cannot write log file in %s (%s) - using console logging only.",
            logs_dir, exc)
