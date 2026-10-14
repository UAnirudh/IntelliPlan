"""Wait for healthy production to serve the exact commit under test."""
import argparse
import json
import math
import re
import time
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import Request, urlopen


def wait_for_deployment(revision, url='https://intelliplan.tech/health', *,
                        timeout=420, interval=10):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Expected a full lowercase Git commit SHA.')
    if not all(math.isfinite(value) and value > 0 for value in (timeout, interval)):
        raise ValueError('Wait and polling durations must be finite and positive.')
    deadline = time.monotonic() + timeout
    attempt = 0
    while (remaining := deadline - time.monotonic()) > 0:
        attempt += 1
        try:
            request = Request(url, headers={'Cache-Control': 'no-cache'})
            with urlopen(request, timeout=min(10, remaining)) as response:
                data = json.load(response)
                if (response.status == 200 and isinstance(data, dict)
                        and data.get('revision') == revision and data.get('status') == 'ok'
                        and data.get('migration_done') is True and data.get('users_schema_ok') is True):
                    print(f'Production serves expected revision {revision} (attempt {attempt}).')
                    return True
        except (URLError, OSError, ValueError, HTTPException):
            pass
        print(f'Attempt {attempt}: expected revision is not healthy yet.')
        time.sleep(max(0, min(interval, deadline - time.monotonic())))
    print(f'Production did not become healthy at revision {revision} within {timeout:g} seconds.')
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    return 0 if wait_for_deployment(args.revision) else 1


if __name__ == '__main__':
    raise SystemExit(main())
