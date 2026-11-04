"""Wait for healthy production to serve the commit under test or its descendant."""
import argparse
import json
import math
import os
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
                healthy = (response.status == 200 and isinstance(data, dict)
                           and re.fullmatch(r'[0-9a-f]{40}', str(data.get('revision', '')))
                           and data.get('status') == 'ok' and data.get('migration_done') is True
                           and data.get('users_schema_ok') is True)
                if healthy:
                    deployed_revision = data['revision']
                    if deployed_revision == revision:
                        print(f'Production serves expected revision {revision} (attempt {attempt}).')
                        return True
                    if is_revision_ancestor(revision, deployed_revision):
                        print('Production serves healthy descendant revision '
                              f'{deployed_revision}, which includes {revision} (attempt {attempt}).')
                        return True
        except (URLError, OSError, ValueError, HTTPException):
            pass
        print(f'Attempt {attempt}: expected revision is not healthy yet.')
        time.sleep(max(0, min(interval, deadline - time.monotonic())))
    print(f'Production did not become healthy at revision {revision} within {timeout:g} seconds.')
    return False


def is_revision_ancestor(expected_revision, deployed_revision, *, repository=None, token=None):
    """Return whether the deployed commit descends from the commit under test.

    A newer main deployment can replace the exact revision while an older main
    workflow is still running its live-site checks. Accepting it is safe only
    after GitHub confirms the deployed commit includes the expected commit.
    """
    if (not re.fullmatch(r'[0-9a-f]{40}', expected_revision)
            or not re.fullmatch(r'[0-9a-f]{40}', deployed_revision)):
        return False
    if expected_revision == deployed_revision:
        return True
    repository = repository or os.getenv('GITHUB_REPOSITORY')
    if not repository or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        return False
    token = token if token is not None else os.getenv('GITHUB_TOKEN')
    compare_url = (f'https://api.github.com/repos/{repository}/compare/'
                   f'{expected_revision}...{deployed_revision}')
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'IntelliPlan-deploy-check'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        request = Request(compare_url, headers=headers)
        with urlopen(request, timeout=10) as response:
            result = json.load(response)
        if not isinstance(result, dict) or result.get('status') != 'ahead':
            return False
        base_commit = result.get('base_commit')
        merge_base_commit = result.get('merge_base_commit')
        commits = result.get('commits')
        return (isinstance(base_commit, dict) and base_commit.get('sha') == expected_revision
                and isinstance(merge_base_commit, dict)
                and merge_base_commit.get('sha') == expected_revision
                and isinstance(commits, list) and bool(commits)
                and isinstance(commits[-1], dict)
                and commits[-1].get('sha') == deployed_revision)
    except (URLError, OSError, ValueError, HTTPException):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    return 0 if wait_for_deployment(args.revision) else 1


if __name__ == '__main__':
    raise SystemExit(main())
