"""Read-only Stripe configuration check; exits nonzero when incomplete."""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelliplan.premium.readiness import check_launch  # noqa: E402


def main():
    load_dotenv()
    key = (os.getenv('STRIPE_SECRET_KEY') or '').strip()

    def retrieve(path):
        response = requests.get('https://api.stripe.com' + path,
                                headers={'Authorization': 'Bearer ' + key}, timeout=20)
        response.raise_for_status()
        return response.json()

    report = check_launch(os.environ, retrieve)
    print(json.dumps(report, indent=2))
    return 0 if report['configuration_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
