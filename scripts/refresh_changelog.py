"""Regenerate the changelog snapshot the weekly newsletter reads.

``.dockerignore`` excludes ``.git``, so the deployed container has no git
history to read. This script captures the last 90 days of commit subjects
into ``intelliplan/email/changelog_snapshot.json``, which *is* shipped in
the image.

Run it before a deploy — or from CI on push to main — so the newsletter's
"what changed" section stays current:

    python scripts/refresh_changelog.py

Only the subject line and date are stored. Bodies are not: they contain
issue links, co-author trailers and internal detail that has no business
near a student-facing email.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "intelliplan" / "email" / "changelog_snapshot.json"
DAYS = 90


def main() -> int:
    since = datetime.utcnow() - timedelta(days=DAYS)
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                f"--since={since:%Y-%m-%d}",
                "--no-merges",
                "--pretty=format:%cI\x1f%s",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=ROOT,
        )
    except Exception as exc:
        print(f"could not run git: {exc}", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(f"git log failed: {result.stderr.strip()}", file=sys.stderr)
        return 1

    commits = []
    for line in result.stdout.splitlines():
        if "\x1f" not in line:
            continue
        date, subject = line.split("\x1f", 1)
        commits.append({"date": date.strip(), "subject": subject.strip()})

    OUT.write_text(
        json.dumps(
            {"generated_at": datetime.utcnow().isoformat(), "commits": commits},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # Report how many survive the newsletter's filter, which is the number
    # that actually matters — a snapshot of 200 commits that yields zero
    # student-facing items is worth knowing about before send day.
    sys.path.insert(0, str(ROOT))
    from intelliplan.email.changelog import humanise

    kept = [c for c in commits if humanise(c["subject"])]
    print(f"wrote {len(commits)} commits to {OUT.relative_to(ROOT)}")
    print(f"{len(kept)} pass the student-facing filter")
    for item in kept[:10]:
        print(f"  · {humanise(item['subject'])['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
