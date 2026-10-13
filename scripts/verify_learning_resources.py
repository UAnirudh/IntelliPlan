"""Check reviewed public link metadata; never ingest or store source lessons."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intelliplan.learning.catalog import RESOURCES  # noqa: E402


def check(url):
    try:
        response = requests.get(
            url,
            timeout=25,
            stream=True,
            headers={
                "User-Agent": "IntelliPlan reviewed educational links verification"
            },
        )
        with response:
            return dict(
                url=url,
                final_url=response.url,
                status=response.status_code,
                reachable=response.status_code == 200,
            )
    except requests.RequestException as error:
        return dict(url=url, reachable=False, error=type(error).__name__)


if __name__ == "__main__":
    urls = sorted({r[key] for r in RESOURCES for key in ("url", "license_url")})
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = dict(
            checked_at=datetime.now(timezone.utc).isoformat(),
            scope="Public metadata links only; HTTP success is not redistribution permission or educational validation.",
            links=list(pool.map(check, urls)),
        )
    output = ROOT / ".codex/verification/school-learning-hub/resource-links.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    sys.exit(0 if all(row["reachable"] for row in result["links"]) else 1)
