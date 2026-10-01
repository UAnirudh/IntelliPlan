"""Trigger the production newsletter from a short-lived Railway cron service."""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PRODUCTION_URL = "https://intelliplan.tech"


def main() -> int:
    secret = os.getenv("CRON_SECRET", "")
    if not secret:
        print("Weekly newsletter cron is missing CRON_SECRET.", file=sys.stderr)
        return 1

    request = Request(
        f"{PRODUCTION_URL}/cron/weekly-newsletter",
        data=b"",
        headers={"X-Cron-Secret": secret, "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"Weekly newsletter request failed ({type(exc).__name__}).", file=sys.stderr)
        return 1

    summary = result.get("summary") or {}
    if result.get("status") != "ok" or summary.get("error") or summary.get("failed", 0):
        print("Weekly newsletter run reported an error; see the web service logs.", file=sys.stderr)
        return 1

    print(
        "Weekly newsletter run complete: "
        f"recipients={summary.get('recipients', 0)} "
        f"sent={summary.get('sent', 0)} skipped={summary.get('skipped', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
