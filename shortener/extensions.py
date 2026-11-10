"""Shared Flask extensions, created once here and attached to the app
in shortener/__init__.py (the factory).
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# In-memory storage on purpose: cPanel runs ONE app worker, so the
# counters live inside the app process itself. No Redis, nothing extra.
# Counters reset when the app restarts - fine for login throttling.
limiter = Limiter(
    key_func=get_remote_address,  # one bucket per visitor IP address
    storage_uri="memory://",
    strategy="fixed-window",
)
