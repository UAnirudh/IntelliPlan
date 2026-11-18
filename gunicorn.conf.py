"""Gunicorn configuration.

The port is read from $PORT here, in Python, instead of being interpolated into
the start command. Railway was passing "$PORT" through to gunicorn literally
(the command wasn't shell-expanded), which crashed with
"'$PORT' is not a valid port number." Reading the env var in this config removes
that dependency entirely — the bind is computed correctly however gunicorn is
launched. Falls back to 8080, the port this service uses.
"""

import os
from pathlib import Path
import subprocess
import sys

bind = f"0.0.0.0:{os.environ.get('PORT', '8080')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "4"))
timeout = 120
max_requests = 500
max_requests_jitter = 50


def when_ready(server):
    """Run one synthetic provider check only on an explicitly named release."""
    revision = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    if not revision or os.environ.get("TUTOR_SMOKE_REVISION") != revision:
        return
    script = Path(__file__).with_name("scripts") / "tutor_deploy_probe.py"
    try:
        subprocess.Popen([sys.executable, str(script)], cwd=Path(__file__).parent)
    except OSError as error:
        server.log.warning("Tutor deployment probe could not start (%s)", type(error).__name__)
