"""Gunicorn configuration.

The port is read from $PORT here, in Python, instead of being interpolated into
the start command. Railway was passing "$PORT" through to gunicorn literally
(the command wasn't shell-expanded), which crashed with
"'$PORT' is not a valid port number." Reading the env var in this config removes
that dependency entirely — the bind is computed correctly however gunicorn is
launched. Falls back to 8080, the port this service uses.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "4"))
timeout = 120
max_requests = 500
max_requests_jitter = 50
