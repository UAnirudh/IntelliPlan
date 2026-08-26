"""Diagnose a Blackboard Learn configuration without revealing secrets.

Blackboard's developer portal issues values for two different protocols and
names some of them confusingly:

  Application ID   also called the LTI *Client ID*. The school's admin
                   registers this. It is NOT a valid REST client_id.
  Application Key  the REST OAuth client_id. A different UUID.
  Secret           pairs with the key.

IntelliPlan connects over REST three-legged OAuth, so it needs the *key*.
Sending the Application ID instead produces, after the student has already
signed in:

    {"code":"illegalArgument","message":"invalid client_id"}

which is also what a school that has not registered the Application ID
returns. This script separates those cases.

Usage:
    python scripts/blackboard_doctor.py
    python scripts/blackboard_doctor.py https://learn.myschool.edu

With an institution URL it also runs the live authorization probe against
that school and reports what Blackboard said.

Values are never printed: only their length, shape, and whether two vars
hold the same value.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional; env may already be exported
    pass

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                     r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: Read by IntelliPlan's REST three-legged OAuth flow.
REST_VARS = (
    "BLACKBOARD_APP_KEY",
    "BLACKBOARD_APP_SECRET",
    "BLACKBOARD_CLIENT_ID",
    "BLACKBOARD_CLIENT_SECRET",
    "BLACKBOARD_APPLICATION_ID",
    "BLACKBOARD_REDIRECT_URI",
    "BLACKBOARD_SCOPE",
)

#: LTI 1.3 registration values. IntelliPlan does not implement LTI, so these
#: are inert — listed so a reader can see they are not the ones in play.
LTI_VARS = (
    "BLACKBOARD_ISSUER",
    "BLACKBOARD_OIDC_AUTH_REQUEST_ENDPOINT",
    "BLACKBOARD_AUTH_TOKEN_ENDPOINT",
)

OK, WARN, BAD, NOTE = "OK  ", "WARN", "FAIL", "NOTE"
#: Prefix for the continuation lines under a finding.
INFO = "  - "


def _get(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _describe(value: str) -> str:
    if not value:
        return "not set"
    shape = "UUID" if UUID_RE.match(value) else f"{len(value)} chars"
    return f"set ({shape})"


def _line(status: str, text: str) -> None:
    print(f"[{status}] {text}")


def report_vars() -> dict[str, str]:
    values = {name: _get(name) for name in REST_VARS + LTI_VARS}

    print("\n== Values IntelliPlan reads (REST 3LO) ==")
    for name in REST_VARS:
        print(f"  {name:<32} {_describe(values[name])}")

    print("\n== LTI 1.3 values (not used by IntelliPlan) ==")
    for name in LTI_VARS:
        print(f"  {name:<40} {_describe(values[name])}")

    return values


def check_credentials(values: dict[str, str]) -> list[str]:
    """Return the list of problems found. Empty means the config looks sane."""
    problems: list[str] = []

    key = values["BLACKBOARD_APP_KEY"] or values["BLACKBOARD_CLIENT_ID"]
    secret = values["BLACKBOARD_APP_SECRET"] or values["BLACKBOARD_CLIENT_SECRET"]
    app_id = values["BLACKBOARD_APPLICATION_ID"]

    print("\n== Checks ==")

    if not key:
        _line(BAD, "No application key. Set BLACKBOARD_APP_KEY to the "
                   "Application Key from the developer portal.")
        problems.append("no key")
    elif values["BLACKBOARD_APP_KEY"]:
        _line(OK, "Application key is taken from BLACKBOARD_APP_KEY.")
    else:
        _line(WARN, "No BLACKBOARD_APP_KEY, so BLACKBOARD_CLIENT_ID is being "
                    "used as the REST client_id.")
        print(f"{INFO}That alias is only correct if it holds the application "
              f"KEY. If it holds the Application ID, every sign-in fails.")
        problems.append("key via legacy alias")

    if not secret:
        _line(BAD, "No secret. Set BLACKBOARD_APP_SECRET.")
        problems.append("no secret")
    elif values["BLACKBOARD_APP_KEY"] and not values["BLACKBOARD_APP_SECRET"]:
        # Half-migrated config: the key comes from the new name, the secret
        # from the old one. Fine if both are from the same portal app, wrong
        # if the old pair is left over from an earlier registration.
        _line(WARN, "The key comes from BLACKBOARD_APP_KEY but the secret from "
                    "BLACKBOARD_CLIENT_SECRET.")
        print(f"{INFO}That pairing only works if both came from the same "
              f"portal application. If the CLIENT_* pair is left")
        print(f"{INFO}over from an earlier registration, the token exchange "
              f"fails with invalid_client after sign-in.")
        print(f"{INFO}Set BLACKBOARD_APP_SECRET to the secret that belongs "
              f"with BLACKBOARD_APP_KEY.")
        problems.append("key and secret come from different variables")

    if key and app_id and key == app_id:
        _line(BAD, "BLACKBOARD_APP_KEY and BLACKBOARD_APPLICATION_ID hold the "
                   "same value.")
        print(f"{INFO}These are two different portal values. The key sent as "
              f"client_id must NOT be the Application ID -")
        print(f"{INFO}Blackboard rejects it on every instance, which looks "
              f"identical to 'no school has approved us yet'.")
        problems.append("key equals application id")

    if key and values["BLACKBOARD_CLIENT_ID"] and key != values["BLACKBOARD_CLIENT_ID"]:
        _line(NOTE, "BLACKBOARD_CLIENT_ID differs from the key in use "
                    "and is being ignored.")
        if app_id and values["BLACKBOARD_CLIENT_ID"] == app_id:
            print(f"{INFO}It matches BLACKBOARD_APPLICATION_ID, which is "
                  f"expected: the LTI Client ID and the Application ID")
            print(f"{INFO}are the same value. Leave it; it is not what the "
                  f"REST flow uses.")

    if not app_id:
        _line(WARN, "BLACKBOARD_APPLICATION_ID is not set.")
        print(f"{INFO}Without it, a student whose school has not approved the "
              f"integration is told to contact support")
        print(f"{INFO}instead of being handed the ID their administrator "
              f"needs to register.")
        problems.append("no application id")

    if key and not UUID_RE.match(key):
        _line(WARN, "The application key is not in UUID form. Portal keys "
                    "normally are.")

    redirect = values["BLACKBOARD_REDIRECT_URI"]
    if not redirect:
        _line(WARN, "BLACKBOARD_REDIRECT_URI is not set; APP_BASE_URL will be "
                    "used. It must match the portal registration exactly.")
    elif not redirect.endswith("/api/lms/callback/blackboard"):
        _line(WARN, f"BLACKBOARD_REDIRECT_URI does not end in "
                    f"/api/lms/callback/blackboard.")
        problems.append("redirect uri path")
    else:
        _line(OK, "Redirect URI points at the Blackboard callback route.")

    scope = values["BLACKBOARD_SCOPE"] or "read offline"
    if "offline" not in scope:
        _line(WARN, f"Scope {scope!r} has no 'offline'. Blackboard will not "
                    f"issue a refresh token, so connections expire in an hour.")
        problems.append("scope missing offline")
    else:
        _line(OK, f"Scope {scope!r} includes offline, so refresh tokens work.")

    if any(values[name] for name in LTI_VARS):
        _line(NOTE, "LTI 1.3 values are present but unused.")
        print(f"{INFO}IntelliPlan connects over the REST API, not LTI. A "
              f"school that registered the LTI tool but not the")
        print(f"{INFO}REST application still rejects sign-in with "
              f"'invalid client_id'. The admin must add the Application ID")
        print(f"{INFO}under System Admin -> Integrations -> REST API "
              f"Integrations, End User Access = Yes.")

    return problems


def probe(institution_url: str) -> None:
    """Run the live authorization probe against one school."""
    import App

    print(f"\n== Live probe: {institution_url} ==")

    normalized = App.normalize_institution_url(institution_url)
    if not normalized:
        _line(BAD, "That URL has no usable host.")
        return
    if normalized != institution_url:
        _line(NOTE, f"Normalized to {normalized}")

    key, _secret = App._blackboard_credentials()
    if not key:
        _line(BAD, "No credentials configured; cannot probe.")
        return

    redirect_uri = App._blackboard_redirect_uri()
    verdict, detail = App._blackboard_preflight(normalized, key, redirect_uri)

    if verdict == "ok":
        _line(OK, "This school accepts the application key. Sign-in will "
                  "reach the Blackboard login and come back.")
    elif verdict == "not_registered":
        _line(BAD, "This school rejected the application key.")
        print(f"{INFO}Blackboard said: {detail}")
        print(f"{INFO}Either the school has not registered the Application ID, "
              f"or the key is the wrong portal value.")
        print(f"{INFO}Students hitting this now see the administrator steps "
              f"instead of the raw API error.")
    else:
        _line(WARN, f"Could not reach that host: {detail}")


def main() -> int:
    print("Blackboard configuration doctor")
    print("(values are never printed - only shape and equality)")

    values = report_vars()
    problems = check_credentials(values)

    if len(sys.argv) > 1:
        probe(sys.argv[1])
    else:
        print("\nPass a school URL to test it live, e.g.:")
        print("  python scripts/blackboard_doctor.py https://learn.myschool.edu")

    print("\n== Summary ==")
    if problems:
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("  No configuration problems found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
