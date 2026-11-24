"""Run the real app locally for blog layout checks.

A throwaway SQLite database and the in-process cron jobs off, so a screenshot
shows the shipped template rather than a stub. The port comes from
BLOG_PREVIEW_PORT (default 5063) rather than PORT: PORT is 0 in some shells,
and Flask then picks a random port, so the screenshot target would move
between restarts. Not imported by the app or the test suite.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SECRET_KEY", "local-preview-only-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///preview-blog.sqlite3")
os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("NOTIFICATIONS_INPROCESS_CRON", "0")
os.environ.setdefault("FOLLOWTHROUGH_INPROCESS_CRON", "0")
os.environ.setdefault("SENTRY_DSN", "")

import App  # noqa: E402

if __name__ == "__main__":
    App.app.run(
        host="127.0.0.1",
        port=int(os.environ.get("BLOG_PREVIEW_PORT", "5063")),
        debug=False,
        use_reloader=False,
    )
