"""Course-only pilot preflight. Live mode requires a test-only token in env.

python scripts/check_school_pilot.py          # local config, no network
python scripts/check_school_pilot.py --live   # authorized synthetic course only
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests

import school_pilot as pilot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not pilot.enabled():
        raise pilot.PilotBlocked('Enable SCHOOL_PILOT before preflight.')
    host, course = pilot.configuration()
    pilot.install()
    assert not pilot.provider_allowed('gemini')
    assert not pilot.provider_allowed('claude')
    # Verify policy denials locally. Do not probe a real unrelated course.
    for url in [f'https://{host}/api/v1/courses/{int(course) + 1}/assignments',
                f'https://{host}/api/v1/files/1',
                'https://api.anthropic.com/v1/messages']:
        try:
            pilot.target('GET', url)
        except pilot.PilotBlocked:
            continue
        raise RuntimeError('Pilot boundary check failed.')
    print(f'PASS: boundary configured for {host}, course {course}.')
    print('Groq text processing:', 'operator-approved' if pilot.provider_allowed('groq') else 'disabled')
    print('Provider-console settings, contracts and Canvas identity permissions are NOT verified by this script.')
    if not args.live:
        print('Live access NOT tested. Use --live with an authorized synthetic test-account token.')
        return
    token = os.getenv('SCHOOL_PILOT_CANVAS_TEST_TOKEN')
    if not token:
        raise pilot.PilotBlocked('Set SCHOOL_PILOT_CANVAS_TEST_TOKEN via a local secret input.')
    headers = {'Authorization': 'Bearer ' + token}
    origin = 'https://' + host
    response = requests.get(origin + '/api/v1/courses', headers=headers, timeout=20)
    response.raise_for_status()
    courses = response.json()
    if len(courses) != 1 or str(courses[0].get('id')) != course:
        raise RuntimeError('Unexpected course response.')
    response = requests.get(origin + f'/api/v1/courses/{course}/assignments',
                            params={'per_page': 1}, headers=headers, timeout=20)
    response.raise_for_status()
    if not isinstance(response.json(), list):
        raise RuntimeError('Unexpected assignment response.')
    print('PASS: authenticated course and first assignment page accessible. No response content printed or saved.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Exceptions from providers/transports can contain tokens or response bodies.
        print(f'FAIL ({type(exc).__name__}). Check configuration, test identity and scopes privately.', file=sys.stderr)
        sys.exit(1)
