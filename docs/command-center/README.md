# Command Center — design dossier

Branch: `feat/command-center`. All work additive; no existing route touched.

| Phase | Document |
|---|---|
| 0 — Architecture audit | [00-phase0-architecture-audit.md](00-phase0-architecture-audit.md) |
| 1 — Product + tech spec | [01-product-and-technical-spec.md](01-product-and-technical-spec.md) |
| 2 — Database design | [02-database-design.md](02-database-design.md) |
| 3 — API design | [03-api-design.md](03-api-design.md) |
| 4 — Frontend design | [04-frontend-design.md](04-frontend-design.md) |
| 5 — AI memory architecture | [05-ai-memory-architecture.md](05-ai-memory-architecture.md) |
| 6 — Implementation roadmap | [06-implementation-roadmap.md](06-implementation-roadmap.md) |

## TL;DR

- The Command Center is the new front door. The student sees the day's briefing, plan, workload forecast, and academic health — all explained — within 5 seconds.
- Code computes facts; AI narrates. Strict separation.
- New `intelliplan/` package (domain + intelligence + repositories + services + api). Zero edits to `App.py`'s 230 existing routes.
- Three new tables (`briefing_cache`, `health_snapshots`, `student_signals`). No edits to the 33 existing models.
- Frontend: Jinja + HTMX + Alpine.js. No build step. No React.
- Feature flag (`command_center_enabled`) gates the whole surface.

## Decisions made under "use your judgment"

1. **Frontend**: HTMX + Alpine.js, not React/Next.
2. **Worker**: HTTP cron, not Redis/RQ. Add a queue when the cache-refresh workload demands it.
3. **Repository duplicate** (`AppStore-Launch/IntelliPlan-Full-Package/Source-Code/`): left untouched in this branch. Tracked for later removal.
4. **Migrations**: stay on the boot-DDL pattern *for these three additive tables only*; Alembic adoption is tracked as the next destructive-change blocker.
5. **Grade band**: US high school is the default model.
6. **Extension**: unchanged.
