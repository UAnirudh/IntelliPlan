"""Report AI provider configuration presence without exposing secrets."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping


def readiness(environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    env = os.environ if environ is None else environ
    approved = env.get("GEMINI_MINORS_CONTRACT_APPROVED", "").strip().lower() in {
        "1", "true", "yes",
    }
    vertex_fields_present = bool(
        env.get("GOOGLE_CLOUD_PROJECT", "").strip()
        and env.get("GOOGLE_CLOUD_LOCATION", "").strip()
    )
    groq = bool(env.get("GROQ_API_KEY", "").strip())
    vertex = approved and vertex_fields_present
    return {
        "groq_configured": groq,
        "vertex_enabled_by_configuration": vertex,
        "vertex_contract_flag_set": approved,
        "vertex_project_and_location_set": vertex_fields_present,
        "developer_api_key_present": bool(
            env.get("GEMINI_API_KEY", "").strip()
            or env.get("GOOGLE_API_KEY", "").strip()
        ),
        "claude_configured": bool(env.get("ANTHROPIC_API_KEY", "").strip()),
        "shared_text_provider_configured": groq or vertex,
    }


def main() -> int:
    state = readiness()
    print("AI provider configuration (presence only; no provider request made)")
    print(f"GROQ_API_KEY: {'configured' if state['groq_configured'] else 'missing'}")
    print(
        "Gemini Vertex: "
        + (
            "enabled by configuration (external credentials and approval not verified)"
            if state["vertex_enabled_by_configuration"]
            else "not enabled (requires written audience exception flag, project, and location)"
        )
    )
    print(
        "Gemini Developer API key: present but not used for server-managed student traffic"
        if state["developer_api_key_present"]
        else "Gemini Developer API key: absent (not required for the Groq baseline)"
    )
    print(
        f"ANTHROPIC_API_KEY: {'configured (paid-plan path only)' if state['claude_configured'] else 'missing'}"
    )
    print(
        "Shared server text provider: "
        + ("configured" if state["shared_text_provider_configured"] else "missing")
    )
    return 0 if state["shared_text_provider_configured"] else 1


if __name__ == "__main__":
    sys.exit(main())
