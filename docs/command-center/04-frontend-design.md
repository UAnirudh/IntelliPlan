# Phase 4 — Frontend Design

## Stack decision

**Jinja + HTMX + Alpine.js.** No build step. No React. Server renders the full Command Center on first paint; HTMX swaps fragments on refresh; Alpine handles local toggles (rationale popovers, theme).

Why not React/Next:
- Forces a build pipeline + separate deploy.
- Doubles surface area for v1 — the rest of the app is Jinja.
- The Command Center is read-mostly. Reactivity is not the bottleneck.
- HTMX matches Flask's strengths.

Why not status-quo Jinja-only:
- We want partial swaps for the refresh button without re-rendering the whole page.
- We want progressive enhancement for offline / slow connection (service worker is already in place).

## Page

New template: `Main_Project/templates/command_center.html`.

Route: `/command-center` (also `/` for authenticated users after a feature flag rollout — see Phase 6 roadmap).

### Layout (mobile-first, single column → 2-column ≥ 768px)

```
┌──────────────────────────────────────────────────────────┐
│ Header   IntelliPlan • Good evening, Anirudh             │
├──────────────────────────────────────────────────────────┤
│ ▌ Briefing card (full width, gradient surface)           │
│   "Chemistry test on Thursday is your top priority…"     │
│   ↻ Refresh    Generated 2m ago • gemini-2.5-flash       │
├──────────────────────────────────────────────────────────┤
│ ▌ Today's plan       │ ▌ Workload (next 7 days)          │
│   1. Chemistry packet│  ▁▃▆█▆▃▁                           │
│      92  ⚠ Critical  │  Heaviest: Wed                    │
│      [why?]          │                                   │
│                      │                                   │
│   2. History notes   │ ▌ Academic health                 │
│      71  ◐ Focus     │   78  (–4 today)                  │
│      [why?]          │   1 overdue • 1 exam in 2d        │
│                      │   [view breakdown]                │
└──────────────────────────────────────────────────────────┘
```

### Components

| Component | Notes |
|---|---|
| `<header-greeting>` | Server-rendered, time-of-day + first name. |
| `<briefing-card>` | Full text from `briefing.body`. HTMX target for `POST /api/today/refresh`. |
| `<plan-list>` | Ordered list, max 7 cards. Each card has score chip, course, due, est-minutes, "why now," and a `[why?]` button. |
| `<priority-chip>` | Compact score (0–100) + tier badge (Critical / Focus / Steady / Light). Color from one variable per tier. |
| `<why-popover>` | Alpine `x-show` panel. On open, HTMX swap `GET /api/today/explain?component=priority&task_id=…` into the panel. |
| `<workload-bars>` | Pure SVG bars from the `forecast.days` array. No chart library. |
| `<health-card>` | Score + delta + 2–3 reason chips. |
| `<empty-state>` | When the student has no tasks: a celebratory card, not a blank page. |
| `<degraded-state>` | When AI is unavailable: the template briefing rendered the same way as the AI briefing. UI is identical except for a subtle "offline briefing" label. |

### Five-second comprehension test

By the time the page finishes painting, the eye should land on:

1. The **headline** of the briefing (largest type on the page).
2. The **#1 task title** + score (next-largest).

Nothing else competes for that initial glance. Everything else fades in slightly delayed (Alpine `x-transition`).

### Styling

Design tokens follow the existing `/dashboard` palette but lean dark-friendly. A single new stylesheet `static/css/command_center.css`. No external CSS framework — keeps the JS budget at ~14kb (Alpine) + 14kb (HTMX) gzipped.

### Accessibility

- All interactive elements are buttons or links — no `div` click handlers.
- Score chips have `aria-label="Priority 92 of 100, critical"`.
- `[why?]` popovers use `aria-expanded` and trap focus on open.
- Workload bars have a hidden text alternative (`<table class="visually-hidden">`).
- Reduced-motion respected.

### Performance budget

- HTML payload < 35 KB gzipped.
- No render-blocking third-party scripts.
- First Contentful Paint < 1.5s on a throttled 4G Pixel 5.
- Largest Contentful Paint < 2.5s.
- Total blocking time < 200ms.

## Service worker

Existing `static/sw.js` is reused. We add `/command-center` and `/api/today` to the runtime-cache list with a stale-while-revalidate strategy so the page opens instantly on cold launches.

## Out of scope for the MVP

- Native iOS / Android shell changes (the existing Capacitor wrapper picks up the new page for free).
- Drag-and-drop reordering of the plan.
- Inline task completion (links out to existing `/tasks/unified`).
- Dark mode toggle (CSS `prefers-color-scheme` only).
