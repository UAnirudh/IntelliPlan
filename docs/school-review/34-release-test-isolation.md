# Release test isolation

The unit job for `bacb2a1` failed once at the first task-completion streak
update with a SQLAlchemy stale-row error; the independent pull-request job
on the same code passed. This is intermittent, rather than evidence of a
deterministically broken streak calculation.

The unit suite uses a single in-memory SQLite connection. The notification
ticker could run during tests, and signup welcome threads could continue
after the calling fixture's database teardown. Session cleanup on that
shared connection can interfere with another transaction. Unit-test defaults
now disable periodic jobs; explicitly invoked job tests remain available.
The suite waits for named welcome delivery threads before continuing the
request, while the dedicated welcome tests retain their inline delivery mock.
Their fixture also creates its schema, independently of import-time setup.

Focused checks, including running streak teardown before welcome setup:
**37 passed**. Independent Python review found no actionable issue in these
scoped changes. No production email/thread behavior is changed or newly
verified. The exact failing interleaving was not deterministically replayed.
Hosted checks on the new commit must pass before release.

The user approved merge and release after passing checks, requested Railway
plugin deployment monitoring, and clarified the next product direction:
fewer visible controls, a primary chat composer, and a scheduler that creates
and adapts an executable plan. These remain ongoing product work, alongside
the incomplete original acceptance ledger and country/language support.
