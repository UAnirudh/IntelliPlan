# Planner route policy coverage — 2026-10-12

The scheduler route mapping now includes `/api/today`, `/api/next-action` and `/api/schedule`, alongside `/api/scheduler`. Previously, an institution's scheduler denial did not stop those actual planner route families before their views ran.

Ten signed-in HTTP regressions cover Today reads/refresh/explanation, next-action reads/explanation/response, and schedule override/apply/feasibility/version endpoints. Each replaces the resolved view with a trap and requires a 403 identifying `scheduler`, proving denial before view/cache/service access. Two initial regression cases failed before the prefix correction. Focused validation: 244 passed, 1788 warnings in 23.20s, exit 0. Independent Python and spec reviewers reported no actionable findings.

This is scoped route denial evidence. It does not establish all background-owner, provider or cache policy coverage. In particular, background briefing refresh still needs an owner-specific audit. The full master brief remains incomplete; this change is local and not deployed.
