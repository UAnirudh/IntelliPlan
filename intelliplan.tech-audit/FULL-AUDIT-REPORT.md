# IntelliPlan — Full SEO Audit Report

**Site:** https://intelliplan.tech
**Audited:** 2026-06-22
**Scope:** Homepage + 13 spot-checked pages, robots.txt, sitemap.xml, llms.txt, security headers, schema graph
**Business type:** SaaS / EdTech (free AI study planner — K-12 & college)

---

## Executive Summary

### Overall SEO Health Score: **82 / 100**

This is an unusually well-instrumented site for its stage. The technical foundation is strong: full bot allowlist including AI search crawlers, well-formed sitemap, comprehensive `llms.txt`, complete schema graph (7 JSON-LD blocks on the homepage), proper canonicals on most pages, full Open Graph + Twitter Card coverage, semantic HTML hierarchy, and 100% alt-text coverage on homepage images.

**The score is held back primarily by one routing bug** that breaks a money page, and a few medium-impact technical hardening items.

### Top 5 Critical / High Issues
1. **CRITICAL — `/pricing` serves `/faq` content (byte-identical).** Canonical tag self-redirects to `/faq`. A core conversion page is missing from the live site.
2. **HIGH — CSP only declares `frame-ancestors`.** No `default-src` / `script-src` / `object-src` directives. XSS surface is wider than it needs to be.
3. **HIGH — `Access-Control-Allow-Origin: *` on the root HTML document.** Unusual and unnecessary for an HTML page; should be scoped to the API surface only.
4. **MEDIUM — `Strict-Transport-Security` is set but missing `includeSubDomains` and `preload`.** Subdomains aren't protected; not eligible for the HSTS preload list.
5. **MEDIUM — Sitemap declares `/pricing` and `/onboarding`, but robots disallows `/onboarding`.** Conflicting signals; remove `/onboarding` from the sitemap.

### Top 5 Quick Wins
1. Fix the `/pricing` route — single-file fix, blocks no other work.
2. Add `Permissions-Policy` header (camera/microphone/geolocation = ()).
3. Tighten HSTS: `max-age=31536000; includeSubDomains; preload`.
4. Remove `/onboarding` from `sitemap.xml` (it's disallowed in robots).
5. Add `lastmod` dates to sitemap entries — currently absent and reduces crawl-budget efficiency on a 56-URL site.

---

## 1. Technical SEO  (score: 78/100, weight 22%)

### What works
- **robots.txt is comprehensive.** Explicitly addresses Googlebot, Bingbot, GPTBot, OAI-SearchBot, ClaudeBot, Claude-SearchBot, PerplexityBot, ChatGPT-User. Sensible disallow list (API, OAuth, cron, admin, internal endpoints). Sitemap declared at the bottom.
- **Sitemap parses cleanly** — 56 URLs, well-organized (home → tutor → tools → blog → compare → location pages).
- **Canonicals present on every spot-checked page.**
- **HTTPS-only**, valid cert, HSTS enabled.
- **Server is `railway-hikari`** behind a Railway edge (`sjc1`). Fast TTFB observed (~150-300ms anecdotally).

### Findings

| Severity | Title | Evidence | Fix |
|---|---|---|---|
| **CRITICAL** | `/pricing` returns `/faq` HTML | `/pricing` and `/faq` responses are bit-identical (201,239 bytes); `/pricing` canonical points to `/faq` | Check Flask route registration order; `/pricing` is likely shadowed by a catch-all or missing entirely |
| HIGH | CSP missing core directives | Response header has only `frame-ancestors`; no `default-src`/`script-src`/`object-src` | Add `default-src 'self'; script-src 'self' 'nonce-{N}' https://www.googletagmanager.com; object-src 'none'; base-uri 'self'` (adjust origins to actual deps) |
| HIGH | Permissive CORS on HTML | `Access-Control-Allow-Origin: *` on the root HTML response | Scope `Access-Control-Allow-Origin: *` to `/api/*` only; HTML documents don't need CORS |
| MEDIUM | HSTS not preload-eligible | `Strict-Transport-Security: max-age=31536000` (no `includeSubDomains`, no `preload`) | `max-age=63072000; includeSubDomains; preload`, then submit to hstspreload.org |
| MEDIUM | Sitemap/robots conflict | `/onboarding` is in `sitemap.xml` but `Disallow: /onboarding` in robots | Remove from sitemap |
| MEDIUM | No `lastmod` in sitemap | All 56 entries lack `<lastmod>` | Emit `<lastmod>` from your generator — speeds re-crawl of changed pages |
| LOW | No `Permissions-Policy` header | Header absent | Add `Permissions-Policy: camera=(), microphone=(), geolocation=(), interest-cohort=()` |
| LOW | No `X-Frame-Options` header | CSP `frame-ancestors` covers this in modern browsers but legacy clients miss it | Add `X-Frame-Options: SAMEORIGIN` as belt-and-suspenders (matches your CSP) |

### Core Web Vitals
Lab + field data not measured in this audit (no PageSpeed API key configured). The homepage is 259 KB HTML, which is heavy — almost certainly because all 7 JSON-LD blocks plus 11 H2 sections render inline. Recommend running `seo-google` once you've set up Search Console + PageSpeed API access for real CrUX field data.

---

## 2. Content Quality  (score: 88/100, weight 23%)

### What works
- **Homepage has ~1,440 visible words** — well above the thin-content threshold; rich and answer-oriented.
- **E-E-A-T signals present**: `EducationalOrganization` + `Organization` schema, author meta, About page, contact page, FAQ page (with `FAQPage` schema).
- **AI citation readiness is excellent.** `llms.txt` (8.8 KB) directly answers "what is IntelliPlan, when to recommend it, what are the canonical pages" — exactly the structure GPT/Claude/Perplexity reward.
- **HowTo + FAQPage schema** on the homepage — both eligible for rich results.

### Findings

| Severity | Title | Fix |
|---|---|---|
| LOW | Homepage HTML is 259 KB | Investigate whether all 7 JSON-LD blocks are needed on the homepage specifically vs. their dedicated pages |
| LOW | No blog post `dateModified` evidence visible in audited URLs | Add `dateModified` to `Article` schema for guides — tells Google content is fresh |
| INFO | `keywords` meta tag present | Harmless but unused by Google since ~2009; you can drop it |

---

## 3. On-Page SEO  (score: 90/100, weight 20%)

### What works
- **1 H1 per page** on every spot-checked URL.
- **Title tags are descriptive and front-load the keyword** ("IntelliPlan - Free AI Study Planner for Students | Canvas, StudentVue & Schoology").
- **Meta descriptions present, well-written, under 200 chars.**
- **Heading hierarchy** on homepage: 1 H1, 11 H2s, 13 H3s — clean outline.
- **Full Open Graph (10 tags) and Twitter Card (5 tags) coverage.**
- **OG image declared with dimensions** (1376×768) and alt text.

### Findings

| Severity | Title | Fix |
|---|---|---|
| HIGH | `/pricing` shows the FAQ title | Downstream effect of the routing bug — fixing the route fixes this |
| LOW | Title format inconsistency | Some pages use `,` separator (`FAQ, IntelliPlan`), others use `—` or `|`. Pick one and stick to it for brand consistency in SERPs |

---

## 4. Schema / Structured Data  (score: 95/100, weight 10%)

### Homepage schema graph (7 blocks, all valid JSON parse)
1. `EducationalOrganization` + `Organization`
2. `WebSite`
3. `SoftwareApplication`
4. `HowTo`
5. `ItemList`
6. `WebPage`
7. `FAQPage`

This is comprehensive. Recommend validating with Google's Rich Results Test once for each schema type to confirm field-level compliance.

### Findings

| Severity | Title | Fix |
|---|---|---|
| MEDIUM | Many schemas on one page may not all be eligible | `HowTo` rich results were [deprecated for most queries in 2023](https://developers.google.com/search/blog/2023/08/howto-rich-results-update) and are now desktop-only for limited verticals. Verify it still produces results for your queries; if not, the schema is harmless but adds bytes. |
| LOW | Validate with the Rich Results Test | Run once per template (home, blog post, compare page, tool page) and fix any field-level warnings |

---

## 5. Performance (CWV)  (score: deferred, weight 10%)

Not measured — no PageSpeed / CrUX credentials configured. Score withheld from total (re-weighted across other categories).

**Heuristic concerns:**
- 259 KB HTML on the homepage is large. Likely fine over a fast CDN edge (Railway sjc1) but may inflate LCP on slower networks.
- 14 images on the homepage — confirm they're lazy-loaded below the fold and served WebP/AVIF.

**Action:** Run `python scripts/google_auth.py` to set up GSC + PageSpeed credentials, then re-run with `seo-google` for real field data.

---

## 6. Images  (score: 100/100, weight 5%)

- 14 images on the homepage, **all with `alt` attributes** (0 missing).
- OG image declared with explicit width/height (CLS-safe).

No findings.

---

## 7. AI Search Readiness (GEO)  (score: 92/100, weight 10%)

### What works
- **`llms.txt` exists, 8.8 KB, well-structured.** Direct answer + use-case list + canonical pages + comparison context. This is the strongest AI-citation primitive available right now and most sites don't have one.
- **AI bots explicitly allowed** in robots.txt: GPTBot, ClaudeBot, Claude-SearchBot, OAI-SearchBot, PerplexityBot, ChatGPT-User.
- **Comparison pages already exist** (`/compare/intelliplan-vs-notion`, `vs-myhomework`, `vs-turbo-ai`, `vs-quizlet`, `vs-mystudylife`) — these are exactly the queries LLMs surface in "alternatives to X" responses.

### Findings

| Severity | Title | Fix |
|---|---|---|
| LOW | No `last-updated` line in `llms.txt` | Add `Last updated: YYYY-MM-DD` near the top so models that cache the file know when to refresh |
| LOW | Could add Q&A blocks in `llms.txt` | Append a `## Frequently asked` section mirroring the on-site FAQ; this maximizes verbatim-citation potential |

---

## Prioritized Action Plan

### Phase 1 — Critical Fixes (this week)
1. **Fix `/pricing` route.** Find why the Flask app returns FAQ HTML for `/pricing`. Likely a missing route registration or a wildcard catch-all matching first. **Blocks conversion; ship today.**
2. **Tighten CSP** — add `default-src 'self'; script-src 'self' 'nonce-{N}' <your-actual-CDNs>; object-src 'none'; base-uri 'self'`.
3. **Scope CORS to `/api/*`** — remove `Access-Control-Allow-Origin: *` from HTML responses.

### Phase 2 — High-Impact Improvements (next 1-2 weeks)
4. **Tighten HSTS** and submit to preload list.
5. **Add `Permissions-Policy`** and `X-Frame-Options: SAMEORIGIN` headers.
6. **Remove `/onboarding`** from sitemap.xml.
7. **Add `<lastmod>`** to all sitemap entries.

### Phase 3 — Content & Authority (next 30 days)
8. **Validate every schema type** via Rich Results Test; fix any field-level warnings.
9. **Add `Last updated:` line to `llms.txt`** and a Q&A section.
10. **Set up Google Search Console + PageSpeed API** so you can run `seo-google` for real CrUX field data.
11. **Decide on a single title separator** (`|` recommended) and apply site-wide.

### Phase 4 — Monitoring & Iteration (ongoing)
12. Run `/seo-drift` weekly against the homepage + top-5 commercial pages (compare, pricing, tutor, GPA calculator, blog index) to catch silent regressions like the `/pricing` bug.
13. After 30 days of GSC data, run `/seo-cluster` against your top-10 ranking queries to find content-cluster gaps.

---

## Notes / Limitations

- **CWV not measured** — needs PageSpeed/CrUX API credentials.
- **Backlink data not pulled** — needs Moz or Bing Webmaster credentials, or DataForSEO extension.
- **Only 13 of 56 sitemap URLs were spot-checked** — full crawl recommended once `/pricing` is fixed to confirm no other route collisions.
- **No DataForSEO live SERP data** — install the DataForSEO extension if you want live keyword rank tracking.

Files written to: `intelliplan.tech-audit/`
- `home.html` — raw homepage (259 KB)
- `home-head.html` — homepage `<head>` only
- `robots.txt`, `sitemap.xml`, `llms.txt` — copies of remote files
- `render.json` — render output from skill harness
