# SEO Fix Brief — IntelliPlan

Hand this whole file to Claude Code. Each section is a self-contained task with file targets, exact code, and acceptance criteria. Work top-to-bottom. Stop after each phase and confirm before moving on.

**Repo:** `C:\Users\uanir\StudioProjects\IntelliPlan`
**Stack:** Flask (Python), deployed on Railway
**Live site:** https://intelliplan.tech

---

## PHASE 1 — Critical (ship today)

### TASK 1.1 — Fix `/pricing` route collision with `/faq`

**Symptom:** `GET https://intelliplan.tech/pricing` returns byte-identical HTML to `/faq`. The response's `<title>` is "FAQ, IntelliPlan…" and the canonical is `<link rel="canonical" href="https://intelliplan.tech/faq">`. The pricing page is effectively missing from the live site.

**Likely causes (investigate in this order):**
1. A catch-all or wildcard route (e.g. `@app.route('/<path>')`) registered before `/pricing`, matching first and serving FAQ content.
2. `/pricing` route is missing entirely and your 404 handler renders FAQ.
3. A reverse-proxy rewrite (Railway config, `nginx.conf`, `Procfile`) rewriting `/pricing` → `/faq`.
4. A template render bug — the `/pricing` view exists but calls `render_template('faq.html')`.

**Steps:**
1. Grep the codebase for route registration:
   ```
   grep -rn "route.*pricing" --include="*.py"
   grep -rn "route.*faq" --include="*.py"
   grep -rn "@app.route" --include="*.py" | head -50
   ```
2. Check route declaration order. Flask matches the first defined rule that fits, so any `@app.route('/<slug>')` defined before `/pricing` will swallow it.
3. Check `Procfile`, `railway.toml`, `nixpacks.toml`, and any `nginx.conf` or middleware files for path rewrites.
4. Hit the route locally: `flask run` then `curl -sI http://localhost:5000/pricing` — confirm you can reproduce.
5. Fix the root cause. **Do not add a redirect** as a workaround — the pricing URL must serve real pricing content.

**Acceptance:**
- `curl -s https://intelliplan.tech/pricing | grep -i "<title>"` returns a title containing "Pricing" (not "FAQ").
- `curl -s https://intelliplan.tech/pricing | grep canonical` returns `href="https://intelliplan.tech/pricing"`.
- The response body byte length differs from `/faq`.

---

### TASK 1.2 — Tighten Content-Security-Policy header

**Current state:** `Content-Security-Policy` is set but only declares `frame-ancestors`. Missing `default-src`, `script-src`, `object-src`, `base-uri`.

**Where to change:** Find where response headers are set. Likely candidates:
- `app/__init__.py` or `app.py` — Flask `@app.after_request` hook
- A `talisman` / `flask-talisman` config
- Middleware in `middleware.py` or similar
- Railway/nixpacks headers config

Grep:
```
grep -rn "Content-Security-Policy\|frame-ancestors\|talisman" --include="*.py"
```

**Replace the CSP value with** (adjust the script/style/connect sources after discovering the real list — see step 2):

```python
csp = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}' https://www.googletagmanager.com https://www.google-analytics.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data: https:; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self' https://www.google-analytics.com; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self' https://lotus-72e3e.web.app https://intelliplan.tech http://localhost:*; "
    "form-action 'self'"
)
```

**Steps:**
1. **Discover real external origins first.** Open `https://intelliplan.tech` in Chrome DevTools → Network tab. List every cross-origin host (fonts, analytics, GTM, Stripe, Firebase, Plausible, Sentry, etc.). Add them to the matching directive.
2. Implement a per-request nonce so you don't need `'unsafe-inline'` for scripts:
   ```python
   import secrets
   @app.before_request
   def gen_nonce():
       g.csp_nonce = secrets.token_urlsafe(16)
   ```
   Then in your base template: `<script nonce="{{ g.csp_nonce }}">`.
3. Ship in **Report-Only mode first** for 48 hours to catch violations without breaking the site:
   ```
   Content-Security-Policy-Report-Only: <the policy above>
   ```
4. Monitor the browser console + any CSP report endpoint, fix violations, then flip the header name to `Content-Security-Policy`.

**Acceptance:**
- `curl -sI https://intelliplan.tech | grep -i content-security-policy` shows all required directives.
- Browser console shows zero CSP violations on home, pricing, blog, compare, tool pages.
- `securityheaders.com` rates the site A or higher.

---

### TASK 1.3 — Scope CORS to the API only

**Current state:** `Access-Control-Allow-Origin: *` is sent on the root HTML response. HTML documents do not need CORS — only XHR/fetch endpoints do. Leaving wildcard CORS on HTML is unnecessary attack surface.

**Where:** Same place CSP is set (`@app.after_request` or middleware). Grep:
```
grep -rn "Access-Control-Allow-Origin\|CORS\|flask_cors" --include="*.py"
```

**Fix:** Only emit CORS headers for paths under `/api/`:

```python
@app.after_request
def cors_for_api_only(resp):
    if request.path.startswith('/api/'):
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Extension-Token'
    else:
        resp.headers.pop('Access-Control-Allow-Origin', None)
        resp.headers.pop('Access-Control-Allow-Methods', None)
        resp.headers.pop('Access-Control-Allow-Headers', None)
    return resp
```

If you use `flask-cors`, configure it with `resources={r"/api/*": {"origins": "*"}}` instead of the global decorator.

**Acceptance:**
- `curl -sI https://intelliplan.tech/ | grep -i access-control` returns nothing.
- `curl -sI https://intelliplan.tech/api/<some-endpoint> | grep -i access-control` returns the headers.
- Chrome extension (if it calls the API from a different origin) still works end-to-end.

---

## PHASE 2 — High-impact hardening (next 1-2 weeks)

### TASK 2.1 — Tighten HSTS for preload eligibility

**Current:** `Strict-Transport-Security: max-age=31536000`

**Replace with:**
```
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
```

**Steps:**
1. Confirm **every** subdomain of `intelliplan.tech` is HTTPS-ready before adding `includeSubDomains`. List them: `dig +short any intelliplan.tech` and check Railway domain settings. If you have an HTTP-only subdomain anywhere, fix that first or skip `includeSubDomains`.
2. Update the header in the same after_request hook.
3. Deploy.
4. After 7 days of stable HSTS in production, submit at https://hstspreload.org/.

**Acceptance:** `curl -sI https://intelliplan.tech | grep -i strict-transport` shows the new value.

---

### TASK 2.2 — Add missing security headers

Add these in the same `@app.after_request` hook:

```python
resp.headers['Permissions-Policy'] = (
    'camera=(), microphone=(), geolocation=(), '
    'interest-cohort=(), payment=(), usb=()'
)
resp.headers['X-Frame-Options'] = 'SAMEORIGIN'  # legacy fallback for CSP frame-ancestors
# Already present, leave as-is:
#   X-Content-Type-Options: nosniff
#   Referrer-Policy: strict-origin-when-cross-origin
```

**Note:** If you accept Stripe payments, drop `payment=()` from `Permissions-Policy` or scope it to your origin.

**Acceptance:** `securityheaders.com/?q=intelliplan.tech` rates A or A+.

---

### TASK 2.3 — Fix sitemap / robots conflict

**Problem:** `sitemap.xml` declares `https://intelliplan.tech/onboarding`, but `robots.txt` has `Disallow: /onboarding`. Conflicting signals — remove from the sitemap.

**Steps:**
1. Find your sitemap generator. Likely candidates:
   ```
   grep -rn "sitemap" --include="*.py"
   ```
   Probably a route like `@app.route('/sitemap.xml')` that returns a generated XML or a static file.
2. Remove the `/onboarding` URL from the URL list.
3. While you're there, also consider removing any other path that's in the `Disallow` list. Audit: pull the disallow list from `robots.txt` and grep sitemap output for matches.

**Acceptance:**
```
curl -s https://intelliplan.tech/sitemap.xml | grep -c onboarding
```
returns `0`.

---

### TASK 2.4 — Add `<lastmod>` to every sitemap URL

**Problem:** All 56 entries lack `<lastmod>`. Google de-prioritizes re-crawling URLs without lastmod hints, which slows propagation of content changes.

**Fix:** Update the sitemap generator to include `<lastmod>` per URL. Sources of truth, in preference order:
1. For blog posts: the post's `updated_at` or `published_at` from your CMS / file frontmatter.
2. For tool pages and static marketing pages: the file's last git-commit timestamp (`git log -1 --format=%cI <path>`).
3. Fallback: deploy timestamp.

Output ISO 8601: `<lastmod>2026-06-20T14:32:00+00:00</lastmod>`.

**Do not** set `lastmod` to "now" on every generation — Google ignores lastmod that obviously updates every crawl. Use real per-URL timestamps.

**Acceptance:** Every `<url>` block in `sitemap.xml` contains a `<lastmod>` with a per-URL date.

---

## PHASE 3 — Content & schema polish (next 30 days)

### TASK 3.1 — Validate every schema type with Google Rich Results Test

Run https://search.google.com/test/rich-results once for each of these page templates and fix any field-level warnings:

| Template | Sample URL | Schemas to validate |
|---|---|---|
| Homepage | `/` | EducationalOrganization, Organization, WebSite, SoftwareApplication, HowTo, ItemList, WebPage, FAQPage |
| Blog post | `/blog/best-ai-study-planner` | Article (verify `dateModified` present) |
| Compare page | `/compare/intelliplan-vs-notion` | WebPage, possibly Product/Review |
| Tool page | `/tools/gpa-calculator` | WebApplication or SoftwareApplication |
| FAQ page | `/faq` | FAQPage |

**Note on HowTo:** Google deprecated HowTo rich results for most queries in August 2023. It's now desktop-only for narrow verticals. Verify it still earns rich results for your specific queries; if not, the schema is harmless but adds bytes — consider removing from the homepage and only emitting it on actual how-to pages.

**Acceptance:** Each template returns "Page is eligible for rich results" with zero errors and zero warnings (warnings on recommended properties are okay; fix what you can).

---

### TASK 3.2 — Add `dateModified` to all Article schema

**Where:** Blog post template (likely `templates/blog/post.html` or similar).

**Add:**
```json
{
  "@type": "Article",
  "datePublished": "{{ post.published_at|iso8601 }}",
  "dateModified": "{{ post.updated_at|iso8601 }}",
  ...
}
```

If you don't track `updated_at`, add a column / frontmatter field and backfill from git history.

**Acceptance:** Every blog post's Article JSON-LD has both `datePublished` and `dateModified`.

---

### TASK 3.3 — Polish `llms.txt`

Edit `/llms.txt` (probably served from `static/llms.txt` or a Flask route):

1. Add a `Last updated: 2026-06-22` line near the top.
2. Append a `## Frequently asked` section that mirrors the on-site FAQ in plain Q&A format — LLMs cite verbatim when the answer is well-formed.

**Acceptance:** `curl -s https://intelliplan.tech/llms.txt | head -5` shows the `Last updated:` line.

---

### TASK 3.4 — Standardize title separator

**Current state:** Mixed separators across pages (`,`, `|`, `—`). Examples observed:
- `IntelliPlan - Free AI Study Planner for Students | Canvas, StudentVue & Schoology`
- `FAQ, IntelliPlan | Canvas, StudentVue & Schoology Study Planner`
- `GPA Calculator — Weighted, Unweighted & Cumulative | IntelliPlan`

**Decide on one format and apply globally.** Recommended: `<Page Topic> | IntelliPlan — <Optional Tagline>`.

Find your title template (likely in a base Jinja template or a `seo_meta()` helper):
```
grep -rn "<title>\|page_title\|seo_title" --include="*.html" --include="*.py"
```

**Acceptance:** Spot-check 10 pages; every `<title>` follows the same `<topic> | IntelliPlan` pattern.

---

### TASK 3.5 — Drop the legacy `<meta name="keywords">` tag

It's been ignored by Google since 2009 and just clutters head. Remove from the base template.

**Acceptance:** `curl -s https://intelliplan.tech/ | grep 'name="keywords"'` returns nothing.

---

## PHASE 4 — Monitoring (set up once, runs forever)

### TASK 4.1 — Set up Google Search Console + PageSpeed API credentials

So you can run real Core Web Vitals audits and indexation checks.

```
python C:\Users\uanir\.claude\skills\seo\scripts\google_auth.py
```

Follow the prompts. Once configured, re-run the audit:
```
/seo audit https://intelliplan.tech
```
and the `seo-google` agent will pull real CrUX field data + GSC indexation status.

---

### TASK 4.2 — Capture an SEO drift baseline

After Phase 1 + 2 ships, snapshot the site so you can detect future regressions like the `/pricing` bug automatically.

```
python C:\Users\uanir\.claude\skills\seo\scripts\drift_baseline.py https://intelliplan.tech
python C:\Users\uanir\.claude\skills\seo\scripts\drift_baseline.py https://intelliplan.tech/pricing
python C:\Users\uanir\.claude\skills\seo\scripts\drift_baseline.py https://intelliplan.tech/tutor
python C:\Users\uanir\.claude\skills\seo\scripts\drift_baseline.py https://intelliplan.tech/compare
python C:\Users\uanir\.claude\skills\seo\scripts\drift_baseline.py https://intelliplan.tech/blog
```

Then add a weekly cron / Railway scheduled job that runs `/seo-drift` and posts diffs to wherever you'll see them.

---

## Out of scope for this brief

- Backlink work (`/seo-backlinks`) — needs Moz or Bing Webmaster Tools credentials first.
- Keyword cluster expansion (`/seo-cluster`) — wait for 30 days of GSC data first.
- E-commerce schema (`/seo-ecommerce`) — N/A, this isn't an e-commerce site.
- Local SEO (`/seo-local`, `/seo-maps`) — N/A, this is a digital-only product.

---

## Order of operations summary

```
PHASE 1 (today, blocks revenue):
  1.1  Fix /pricing routing collision      ← CRITICAL
  1.2  CSP hardening (report-only → enforce)
  1.3  Scope CORS to /api/* only

PHASE 2 (next 1-2 weeks):
  2.1  HSTS + preload submission
  2.2  Permissions-Policy + X-Frame-Options
  2.3  Remove /onboarding from sitemap
  2.4  Add <lastmod> to sitemap

PHASE 3 (next 30 days):
  3.1  Validate schema with Rich Results Test
  3.2  Add dateModified to Article schema
  3.3  Polish llms.txt
  3.4  Standardize title separator
  3.5  Drop <meta keywords>

PHASE 4 (set-and-forget):
  4.1  Google API credentials
  4.2  SEO drift baselines + weekly check
```

After each phase, re-run:
```
/seo audit https://intelliplan.tech
```
and confirm the score improved before moving on.
