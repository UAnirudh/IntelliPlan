# Branch audit — 2026-09-25

Snapshot after fetching and pruning `origin`, with `origin/main` at `15344a8`.

## Main and active work

- Local `main` was 13 commits behind and was fast-forwarded to `origin/main` before Foundations work started. It had no uncommitted changes.
- Foundations was built on `codex/primer-foundations`, tested, and merged into `main` as `25f510c`.
- The separate `feat/mobile-app` worktree has thousands of tracked files showing as deleted and its local branch is 160 commits behind its remote. Its path is under an older Claude temporary scratchpad. This audit did not restore, remove, or commit those deletions because their intent cannot be inferred from Git status alone.

## Remote branch status

The following remote branches are ancestors of main and contain no unmerged commits: `claude/hopeful-pascal-t4qeuz`, `claude/onboarding-email-play-store-r522qp`, `claude/seo-dead-internal-link`, `claude/seo-duplicate-metadata`, `claude/seo-sitemap-noindex-orphans`, `codex/fix-dashboard-overflow-active-study`, `codex/fix-dependabot-security`, `codex/landing-stats-refresh`, `feat/consented-insight`, `feat/growth-revenue`, `feat/orbit-export`, `feat/reland-with-boot-safety`, `feat/ui-finish-sidebar-rail-rag`, `fix/demo-polish-e2e`, and `revert/pr-42`.

Four remote branches still have commits outside main but have no patch-unique commits according to `git cherry`: `claude/pensive-newton-uuq0gx`, `claude/website-issues-bugs-812jm5`, `feat/command-center-surface`, and `feat/mobile-app`. Their changes appear to have been reapplied or superseded; merging the old history would add no patch-unique work.

Five branches have patch-unique changes that require their own review:

| Branch | Unique commits | Scope | GitHub state |
| --- | ---: | --- | --- |
| `claude/award-badges-color-modes-518gb1` | 4 | awards, sign-in and CORS fixes, Canvas overdue grading | no open PR |
| `claude/design-system-extraction-el4gea` | 3 | marketing carousel and video scripts | no open PR |
| `claude/optimistic-cannon-kzyzey` | 3 | Canvas OAuth and district LMS extension sync | draft PR #32, mergeable at audit time |
| `claude/vibrant-mccarthy-98y7sq` | 1 | older phone UI and grade fixes | draft PR #31, conflicting at audit time |
| `claude/zen-rubin-yg88jy` | 1 | Playwright test handling for collapsed FAQ links | no open PR |

The collapsed FAQ link check on `zen-rubin-yg88jy` is already present in `main` with different source formatting. The Canvas course-total and `undefined` fixes from the older phone branch also appear in today's `canvas_helper.py` and grade templates; its remaining mobile changes need separate review.

The older draft PRs are separate scopes from Foundations. PR #31 needs a fresh reconciliation against today's templates; PR #32 needs its Canvas and extension acceptance checks run against today's main. The award/security and marketing branches need their own product and test review before merging. No unresolved merge conflict exists in the Foundations branch itself.

## Refresh — 2026-09-26

After `git fetch --all --prune`, `origin/main` remained at `7fd7cd4`. The current Foundations expansion branch has no unmerged index entries. The old `feat/mobile-app` worktree still has 392 tracked changes, beginning with deletions in `.claude/`; this is not a clean branch to move, reset, or merge without its owner's review.

The two open PRs remain drafts. [PR #31](https://github.com/UAnirudh/IntelliPlan/pull/31) is `CONFLICTING`; a read-only merge-tree check found conflicts in `base.html`, `dashboard.html`, `grademodel.html`, and `canvas_helper.py`. It also has an old failed Cloudflare Workers build check. [PR #32](https://github.com/UAnirudh/IntelliPlan/pull/32) is `MERGEABLE` but `UNSTABLE` because its Cloudflare Workers build check failed; its test and security checks passed at the last recorded run. These are independent feature branches, so neither was folded into the Foundations release or force-updated as part of resolving its own branch.
