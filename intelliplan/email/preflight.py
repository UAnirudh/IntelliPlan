"""Is the email system actually able to send right now?

Every failure this catches is one that is otherwise invisible until a
student does not get an email, or gets one that bounces when they reply.
The checks are deliberately blunt and specific: each returns a status, the
thing that is wrong, and the single action that fixes it.

Three severities:

* ``fail``  — a send will not work, or will be non-compliant. Fix first.
* ``warn``  — a send works but something is degraded (replies bounce,
              deliverability suffers).
* ``ok``    — nothing to do.

The domain check asks Resend's own API whether the sending domain is
verified. That is more reliable than inspecting DNS from inside a container
that may have no resolver tooling, and it is the same answer the provider
will act on when the send happens.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"<([^>]+)>|^(\S+@\S+)$")


def _domain_of(address: str) -> str:
    """Pull the domain out of either `Name <a@b.com>` or `a@b.com`."""
    address = (address or "").strip()
    match = _ADDR_RE.search(address)
    bare = (match.group(1) or match.group(2)) if match else address
    return bare.rsplit("@", 1)[-1].strip().lower() if "@" in bare else ""


def _resend_domains(api_key: str) -> list[dict] | None:
    """Ask Resend which domains exist and whether they are verified.

    Returns None when the call fails — an unreachable provider is a
    different problem from an unverified domain and must not be reported
    as one.
    """
    try:
        req = urllib.request.Request(
            "https://api.resend.com/domains",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
        data = payload.get("data", payload)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.info("could not list Resend domains: %s", exc)
        return None


def check() -> dict:
    """Run every check. Returns {"ok": bool, "checks": [...]}."""
    from . import templates

    checks: list[dict] = []

    def add(name, status, detail, fix=""):
        checks.append({"name": name, "status": status, "detail": detail, "fix": fix})

    # ── Can we send at all? ──────────────────────────────────────────
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    smtp_host = (os.getenv("SMTP_HOST") or os.getenv("MAIL_HOST") or os.getenv("EMAIL_HOST") or "").strip()
    if api_key:
        add("provider", "ok", "Resend is configured.")
    elif smtp_host:
        add("provider", "warn", f"No RESEND_API_KEY; falling back to SMTP at {smtp_host}.",
            "Resend is the primary path and has better deliverability. Set RESEND_API_KEY.")
    else:
        add("provider", "fail", "Neither RESEND_API_KEY nor SMTP is configured. Nothing can send.",
            "Set RESEND_API_KEY, or SMTP_HOST/SMTP_USER/SMTP_PASSWORD.")

    # ── Is the From domain verified with the provider? ───────────────
    from_addr = os.getenv("RESEND_FROM", "IntelliPlan <noreply@intelliplan.tech>")
    from_domain = _domain_of(from_addr)
    if not from_domain:
        add("from_address", "fail", f"RESEND_FROM is not a usable address: {from_addr!r}",
            'Use the form "IntelliPlan <noreply@yourdomain>".')
    elif api_key:
        domains = _resend_domains(api_key)
        if domains is None:
            add("from_domain", "warn", "Could not reach the Resend API to check domain status.",
                "Check the API key and network, then re-run.")
        else:
            match = next(
                (d for d in domains if from_domain == d.get("name")
                 or from_domain.endswith("." + str(d.get("name")))),
                None,
            )
            if match is None:
                add("from_domain", "fail",
                    f"{from_domain} is not registered in Resend. Sends will be rejected.",
                    f"Add {from_domain} at resend.com/domains and publish the DNS records it gives you.")
            elif str(match.get("status", "")).lower() != "verified":
                add("from_domain", "fail",
                    f"{from_domain} is registered but its status is {match.get('status')!r}.",
                    "Publish the DKIM/SPF records Resend lists, then press Verify.")
            else:
                add("from_domain", "ok", f"{from_domain} is verified with Resend.")
    else:
        add("from_domain", "warn", f"Cannot check {from_domain} without RESEND_API_KEY.", "")

    # ── Will replies reach a human? ──────────────────────────────────
    reply = templates.reply_to()
    support = templates.support_email()
    reply_domain = _domain_of(reply)
    if reply_domain and reply_domain == from_domain:
        # Same domain as the sender. That only receives if the domain has MX
        # records, which a send-only setup does not need and often lacks.
        add("reply_to", "warn",
            f"Replies go to {reply}, on the same domain you send from. "
            "That address only receives mail if the domain has MX records.",
            "Confirm you can receive at that address. If not, point "
            "MARKETING_REPLY_TO at a mailbox that works today.")
    elif reply:
        add("reply_to", "ok", f"Replies go to {reply}.")
    else:
        add("reply_to", "fail", "No reply address configured.", "Set MARKETING_REPLY_TO.")

    if support != reply:
        add("support_email", "ok", f"The welcome email points support at {support}.")

    # ── Compliance ───────────────────────────────────────────────────
    if templates.postal_address():
        add("postal_address", "ok", "MARKETING_POSTAL_ADDRESS is set.")
    else:
        add("postal_address", "fail",
            "MARKETING_POSTAL_ADDRESS is unset. The newsletter and feedback emails "
            "will refuse to send.",
            "Set it to a real postal address. CAN-SPAM requires one in commercial email.")

    # ── Cron + links ─────────────────────────────────────────────────
    if os.getenv("CRON_SECRET", "").strip():
        add("cron_secret", "ok", "CRON_SECRET is set; the cron endpoints are reachable.")
    else:
        add("cron_secret", "fail",
            "CRON_SECRET is unset. Every cron endpoint returns 503 and nothing sends.",
            "Set CRON_SECRET and use it in the X-Cron-Secret header.")

    base = (os.getenv("APP_BASE_URL") or "").strip()
    if base.startswith("https://"):
        add("app_base_url", "ok", f"Unsubscribe links will point at {base}.")
    elif base:
        add("app_base_url", "warn", f"APP_BASE_URL is {base!r}, not https.",
            "Unsubscribe links in a sent email must be publicly reachable over https.")
    else:
        add("app_base_url", "fail", "APP_BASE_URL is unset; unsubscribe links will be wrong.",
            "Set APP_BASE_URL to the public https origin.")

    return {
        "ok": not any(c["status"] == "fail" for c in checks),
        "failures": sum(1 for c in checks if c["status"] == "fail"),
        "warnings": sum(1 for c in checks if c["status"] == "warn"),
        "checks": checks,
    }
