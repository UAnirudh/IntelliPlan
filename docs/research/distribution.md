# IntelliPlan distribution research: where and how to spread it

_Research date: 2026-09-30. Sources are WebSearch results, linked inline. Reddit and most social-stats sites were blocked by this session's egress policy, so subreddit sizes and creator follower counts come from third-party snapshots and **must be re-checked before use**. Reddit also stopped showing public subscriber counts in September 2025; it now shows weekly visitors and contributions ([Dexerto](https://www.dexerto.com/entertainment/reddit-replaces-public-member-counts-with-new-metrics-3249643/), [ideafast](https://www.ideafast.pro/blog/subreddit-stats-guide)). "Members" below therefore means the last public or third-party figure._

## Starting position (what the repo already gives us)

| Asset | Where | Distribution status |
|---|---|---|
| Free product; Pro at $5/month only lifts AI caps | `Main_Project/templates/pricing.html` | Strong hook: "free forever, no locked planner features" |
| Referral system and ambassador tiers | `/ref/<code>`, `/api/referral`, `/ambassador`, `/ambassador/dashboard` | Tiers: Bronze (1 referral), Silver (5 → 5 months of Pro), Gold (12 → a year plus a feature). **Pays only in Pro months**, and Pro only lifts AI caps, so the incentive is weak for creators |
| Schools page and lead capture | `/schools`, `/api/school-outreach` (`SchoolOutreachLead` model) | Claims COPPA compliance and read-only access. **No DPA, SDPC or third-party privacy rating is mentioned** |
| SEO surface | 8 blog posts, 5 compare pages, 7 free tools (`/tools/*`), IndexNow (`/api/admin/indexnow/submit`) | A good base. No platform-specific tool pages yet |
| Chrome extension | `extension/` (MV3, v1.1.0) | **Not on the Chrome Web Store.** Ships as zip files in the repo; no store link in any template |
| Mobile app (Expo, iOS and Android) | `mobile/`, `android-version/` (Play packet), `AppStore-Launch/` | **Not published.** The Play packet is ready; iOS needs a Mac and a $99/year account |
| Desktop app | `desktop/`, `/download` | Live |
| Discord | discord.gg/34FYWhJQMU (README) | Exists |
| Launch badges | Dev Lanz, Launch Finds, LaunchIt (README) | Small launch sites only. No Product Hunt or AlternativeTo presence found by search |
| Name collision | "Intelliplan AB" is a Swedish HR/staffing software company ([LinkedIn](https://www.linkedin.com/company/intelliplan-ab), [Crunchbase](https://www.crunchbase.com/organization/intelliplan-ab)) | Brand searches are contested. Use "IntelliPlan study planner" in titles and store names |

**Key competitive fact for distribution:**
- Canvas helper extensions have huge installed bases: Tasks for Canvas at 1,000,000 users and BetterCampus at about 2M ([canvascope](https://www.canvascope.org/compare/best-canvas-chrome-extensions), [chrome-stats](https://chrome-stats.com/d/tasks-for-canvas)).
- The AI study apps that broke out grew through paid UGC and ambassador networks. StudyFetch's creator network drove a reported 547.5M views, and Turbo AI's 100+ ambassadors took it to about 4M users ([superscale](https://superscale.ai/learn/tiktok-ugc-strategy-how-to-go-viral-for-your-app-in-2025/), [stormy.ai](https://stormy.ai/blog/coconote-ugc-playbook-app-growth-2026)). These are third-party figures and unaudited.

**Measurement prerequisite.** Tag every channel with UTM parameters or a referral code, and define activation as *connected an LMS and generated a first plan*. `analytics.py` (PostHog) and `intelliplan/growth/` already exist. Judge channels on activated users, not visits.

---

## 1. Reddit

General rule: most student subreddits ban or tightly limit self-promotion. The working norm is about 90% genuine help to 10% self-reference, and 61% of founder-targeted subs ban promotion outright ([oneup study](https://oneup.today/blogs/reddit-selfpromo-rules-study-2026), [redship](https://redship.io/blog/reddit-self-promotion-rules)). The compounding play is to post **genuinely useful free tools** (the no-login grade calculators) and write-ups, not app pitches.

| Subreddit | Members (source, date) | Self-promo rules | Fit | First action |
|---|---|---|---|---|
| r/GetStudying | ~3.3M ([reddapi snapshot](https://reddapi.dev/subreddits/getstudying/insights)) | **Unverified.** Could not load the rules. Assume no promotion | High: study planning | Post a genuinely useful "how I plan finals week from my Canvas feed" guide; link only if asked |
| r/college | ~2.8M ([thehiveindex](https://thehiveindex.com/topics/students/platform/reddit/)) | Unverified; assume no promotion | Medium–high | Answer "how do you keep track of assignments" threads with the method, not the app |
| r/ApplyingToCollege | ~1.3M ([hiveindex](https://thehiveindex.com/communities/r-applyingtocollege/)) | Unverified, and strict in practice | Low (admissions, not planning) | Skip |
| r/SideProject | ~795K ([hiveindex](https://thehiveindex.com/communities/r-sideproject/)) | **Promotion allowed** with a story and context; bare landing-page drops get removed ([mediafa.st](https://www.mediafa.st/what-subreddits-allow-self-promotion)) | Builder audience, not students | One "built by a high schooler" story post. Drives backlinks and feedback, not users |
| r/SAT | ~682K ([gummysearch](https://gummysearch.com/r/Sat/)) | Unverified | Medium (test-date planning) | Share an "SAT countdown plan" tool, if one is built |
| r/APStudents | ~361K ([hiveindex](https://thehiveindex.com/communities/r-apstudents/)); linked AP Students Discord ~54K ([Discord](https://discord.com/servers/ap-students-181970867549503489)) | Unverified | **High**: multiple APs are a planning problem; `/blog/ap-study-planner` and `/library` exist | In April, post an AP exam-schedule planner resource; ask mods before posting any link |
| r/GCSE ~194K, r/6thForm ~186K, r/IBO ~183K | ([hiveindex](https://thehiveindex.com/topics/students/platform/reddit/), [gummysearch](https://gummysearch.com/r/GCSE/)) | Unverified | Medium; `/uk` page exists | Exam-season (May–June) revision-timetable resource |
| r/HomeworkHelp | ~160K (older figure) | Unverified | Low | Skip |
| r/productivity | Large | **Bans self-promotion in any form**, including when asked for recommendations ([redditgrowthdb](https://www.redditgrowthdb.com/database/subreddits/productivity)) | — | Do not post |
| r/ADHD | Large | Bans "I built an ADHD app" posts; app promotion only in designated threads ([oneup](https://oneup.today/blogs/reddit-selfpromo-rules-study-2026)) | High need, hostile to promotion | Only in designated threads, and only after the breakdown and Focus Shield features exist |
| r/alphaandbetausers | Small | Built for beta requests: one post per product per month, and the build must be testable ([mediafa.st](https://www.mediafa.st/what-subreddits-allow-self-promotion)) | Feedback | Recruit the **12 Google Play closed-test testers** here (see §5) |
| r/InternetIsBeautiful | ~16.6M | No downloads, no freemium, no stores or demos ([mediafa.st](https://www.mediafa.st/what-subreddits-allow-self-promotion)) | Maybe: a **no-login** grade calculator page could qualify. Unverified | Read the rules; if a standalone tool page qualifies, submit `/tools/final-grade-calculator` once |

**Expected reach, cost and effort.**
- Reach: posts that land get about 10k–100k views; most get fewer than 1k. This is an estimate.
- Cost: $0.
- Effort: high and ongoing, with ban risk.
- **Verdict:** a supporting channel, not a pillar. It doesn't compound unless the posts are useful resources that keep ranking in Google, which Reddit threads often do.

---

## 2. Discord

| Server | Size | Fit | First action |
|---|---|---|---|
| Study Together | ~1.05–1.08M members ([top.gg](https://top.gg/discord/servers/542882462976860160), [Discord education](https://discord.com/servers/education)) | Very high: people who are studying right now | Email the staff to sponsor a finals-season "study marathon" event or giveaway (Pro months plus merch). Ask about a partner channel. Cost unknown; negotiate |
| AP Students (r/APStudents) | ~54K ([Discord](https://discord.com/servers/ap-students-181970867549503489)) | High (AP) | Offer mods free AP-planning resources for exam season; ask before sharing links |
| Fiveable community | Large (size unverified) | High (AP) | Content partnership, not promotion; see §7 |
| IntelliPlan's own server | Small (unknown) | Retention and feedback | Turn it into the ambassador HQ: a creator brief channel, a weekly leaderboard of *activated* referrals |

- **Cost:** $0–$500 per sponsored event (estimate).
- **Effort:** low to medium.
- **Reach:** thousands per event (estimate).

---

## 3. Short-form video: StudyTok, StudyGram, StudyTube

**Why this channel.** The #studytok tag has about 3.8M posts ([CORQ](https://corq.studio/insights/study-influencers-creators-sharing-revision-methods-and-student-life-vlogs-ahead-of-exam-season-2026/)). The student apps that broke out (StudyFetch, Turbo, Coconote) did it with UGC networks. The format that works is the **"app flash"**: an aesthetic study-with-me or "how I plan my week" video where the app is on screen for 1–2 seconds, not a hard ad ([stormy.ai](https://stormy.ai/blog/coconote-ugc-playbook-app-growth-2026)).

**Going rates (2026).**
- TikTok micro creators (10K–100K followers), education niche: about $150–1,000 per video.
- YouTube micro integrations: about $500–3,000.
- Sources: [influencerfee](https://influencerfee.com/blog/education-influencer-pricing/), [usesnippet](https://usesnippet.app/tools/influencer-rates/education), [hubfluence](https://www.hubfluence.io/tiktok-influencer-rates).

**Niches, in priority order:**
1. **High-school AP and "multiple APs" planning.** This is IntelliPlan's core: StudentVUE and Schoology users are K-12.
2. **College "how I organize Canvas."**
3. **ADHD study tips.** High need, but be careful with medical claims.
4. **Nursing and pre-med.** Heavy workload, many creators.
5. **UK GCSE and A-level revision timetables.** `/uk` already exists.

**Example creators in or near the 10K–500K range.** Every count is from a third-party snapshot. **Verify before contacting.**

| Creator | Platform | Reported size | Niche | Source |
|---|---|---|---|---|
| @bilan.caliii | TikTok | ~71.3K | Law student study tips | [CORQ](https://corq.studio/insights/study-influencers-creators-sharing-revision-methods-and-student-life-vlogs-ahead-of-exam-season-2026/) |
| @tamidollars | TikTok | ~61.3K | BSN (nursing) student | [Modash](https://www.modash.io/find-influencers/tiktok/united-states/student) |
| @_mixourdreams_ | Instagram | ~50K | Studygram, desk setups | [Instagram](https://www.instagram.com/_mixourdreams_/) |
| Better Creating | YouTube | ~230K | Notion and productivity builds (pairs with the Notion sync) | [search summary](https://selfmanager.ai/articles/top-productivity-youtube-channels-to-follow-in-2026) |
| @littlemabu ("Mabu The Student") | TikTok | ~517K (just above range) | Student content, 3M average views | [Modash](https://www.modash.io/find-influencers/tiktok/united-states/student) |
| @livviazhang (Olivia Zhang) | TikTok | Size unverified | AP exams and study tips | [TikTok discover](https://www.tiktok.com/discover/how-to-dtudy-for-an-ap-exam-olivia-zhnag) |
| @history_4_humans (Dan Lewer) | TikTok | Size unverified | APUSH teacher | [TikTok discover](https://www.tiktok.com/discover/how-to-study-for-apush-exam-2026) |
| @tineocollegeprep | TikTok | Size unverified | AP score analysis | same |
| @christiaanhenny, @streetpidgeon, @emonthebrain | TikTok | Sizes unverified | ADHD study methods | [TikTok discover](https://www.tiktok.com/discover/study-tips-for-adhd) |
| @sprinklestudies, @reemsdesk | Instagram | Sizes unverified | Nursing studygram; desk setups | [hypeauditor / roundup](https://hypeauditor.com/top-instagram-education/) |

The large names (Gohar Khan at 3.4M, Mike Dee at 1.2M, Cajun Koi Academy at 1.4M, UnJaded Jade at 1M+, Ruby Granger at 900K+) are **outside the requested range** and too expensive for now ([Wikipedia](https://en.wikipedia.org/wiki/Gohar_Khan_(internet_personality)), [Social Blade](https://socialblade.com/youtube/handle/mikedeeofficial)).

**How to find more, repeatably.**
- Use TikTok Creator Marketplace, Passionfroot ([passionfroot](https://www.passionfroot.me/creators)) and Collabstr, filtered for "student, study" and 10K–150K followers, US audience.
- Prefer creators whose *average views* are at least 20% of their followers.

**Program design.** This is an upgrade of `/ambassador`: turn the ambassador program into a **creator program**.
- **Pay:** a flat fee per posted video ($100–300 to start), plus a **cash bounty per activated referral**, for example $1–2. Keep the Pro-month tiers for regular student ambassadors.
- **Why cash:** Pro only lifts AI caps, so it is not a real incentive for creators.
- **Brief:** hand each creator 5 proven hooks:
  1. "I stopped planning, and my Canvas plans itself."
  2. "What I need on my final" (grade modeler).
  3. The StudentVUE grade-drop reaction video.
  4. "Study with me" with IntelliPlan's Active session on screen.
  5. The parent-dashboard angle for family creators.
- **Tracking:** every creator gets a `/ref/<code>` link. `ambassador_dashboard.html` shows their activations.

**Numbers.**
- **Reach:** about 5–50K views per micro-creator video. Heavy tail; roughly 1 in 10 videos carries the month. These are estimates.
- **Cost:** $2–5K per month for 15–25 videos.
- **Effort:** medium (sourcing and briefing), falling over time.
- **Compounding:** yes, if the best-performing videos are re-used as Spark Ads or whitelisted. Posting from IntelliPlan's own TikTok account (3–5 short demos a week) is free and also compounds.

---

## 4. Schools and districts

### 4a. Student-side channels, which need no district approval

| Channel | Mechanism | Reach, cost, effort | First action |
|---|---|---|---|
| **Campus and school ambassadors** (build on `/ambassador`) | A student introduces IntelliPlan to their school's clubs, student government and study groups | 50–500 students per active ambassador (estimate); cost is Pro months plus merch; medium effort | Add a "school leaderboard" to `/ambassador/dashboard` (activated students per school), and a printable one-page flyer with a QR code to `/ref/<code>` |
| **Student government, NHS and peer tutoring** | NHS tutoring chapters and study-hall programs need an organizing tool | Tens to hundreds per school; free; low effort | Give ambassadors an outreach template: "free tool for our tutoring program" |
| **Paid ambassadors on the Knowt model** | Knowt pays college ambassadors, and **pays teacher ambassadors $1,000 up front** ([Knowt Knights](https://knowt.com/teachers/knowt-knights-teacher-ambassadors), [Polymer](https://jobs.polymer.co/knowt-inc/38258)) | Only once there is budget | Defer |

### 4b. Teacher and counselor outreach

- **Counselors.** They own "study skills," "missing work" and 504/IEP organization accommodations. The **Grade Pulse** alerts and the parent dashboard (`/parent_dashboard`) are the hooks. The ASCA Annual Conference draws about 5,000 counselors; 2027 is in Columbus ([ASCA brochure](https://www.schoolcounselor.org/getmedia/43649f2e-1eda-4cbb-bfae-bbeb32aed231/2026-ex-sp-brochure.pdf), [ASCA 2027](https://ascaconferences.org/2027/)). Exhibitor pricing was not retrieved; it is likely thousands of dollars, so defer. Also consider state counselor associations, which have cheaper booths (unverified).
- **Teacher communities.** For example, "Teach With Tech" on Facebook has about 61K members ([WeAreTeachers](https://www.weareteachers.com/teacher-facebook-groups/)). Share the free tools, not the app.
- **First action.** Pull the schools with the most active users from the DB (StudentVUE district URLs are a strong signal). Email each school's counselor a one-pager with the subject "X of your students already use this; here's our privacy packet." Log each one via `/api/school-outreach`.

### 4c. District approval: the trust layer

This is the gate for any school-level adoption, and it gets **harder after the April–May 2026 Canvas breach**. That breach is reported at 231M people across about 9,000 schools, with a reported ransom payment ([Wikipedia](https://en.wikipedia.org/wiki/2026_Canvas_data_breach), [McDonald Hopkins](https://www.mcdonaldhopkins.com/insights/news/the-instructure-canvas-incident-what-happened-and-whats-next)). IntelliPlan holds LMS tokens and **StudentVUE credentials**. `secret_box.py` encrypts them at rest; say so explicitly in the schools packet.

| Mechanism | What it is | Cost | Effort | First action |
|---|---|---|---|---|
| **SDPC National DPA (NDPA) plus Exhibit E** | The Student Data Privacy Consortium's standard agreement. Once one district signs with a vendor, other districts can **subscribe via Exhibit E** without re-negotiating. Vendors can have DPAs posted on the SDPC Resource Registry **without paying for membership** ([SDPC](https://privacy.a4l.org/), [Registry](https://sdpc.a4l.org/), [IU13](https://www.iu13.org/administrators/statewide-software-sales/software-for-schools/sdpc/)) | $0 for the registry. Legal review is time | M (one-off) | Get one friendly district, where IntelliPlan already has users, to sign the NDPA with a general Exhibit E. That single signature is reusable in that state's alliance. **Compounding.** |
| **State laws** | Illinois SOPPA: a DPA with *each* district, posted publicly, 30-day breach notice ([ISBE summary via IMSA](https://www.imsa.edu/student-life/soppa-compliance/), [edulens](https://edulens.net/student-data-privacy/illinois)). California CSPA ([CITE](https://www.cite.org/stuprivacy)). Texas TEC cooperative ([TEC](https://tec-coop.org/data-privacy/)). NY Ed Law 2-d (details not retrieved) | $0 | M | Write a single "State privacy addendum" page that maps IntelliPlan's practices to SOPPA, CSPA and 2-d |
| **Common Sense Privacy evaluation** | Free vendor evaluation with a pre-release "redress period." Pass, Warning or Fail ratings that districts look up ([Common Sense Privacy](https://privacy.commonsense.org/), [FAQ](https://www.commonsense.org/education/reviews/FAQ)) | Free | S | Email a request to evaluate IntelliPlan. Fix issues during redress. Show the badge on `/schools` |
| **1EdTech TrustEd Apps Seal** | Formal privacy certification. Non-members pay **$2,000 per module or $10,000 per bundle** ([1EdTech FAQ via search](https://www.1edtech.org/certification/data-privacy/faq), [TACL](https://www.1edtech.org/program/tacl)) | $2–10K | M | **Defer.** Not worth it before district deals exist |
| **Schools page upgrade** | `schools.html` currently claims COPPA compliance and read-only access only | Free | S | Add a "Privacy packet" section: signed NDPA, subprocessors list (Gemini, Groq, Railway, Sentry, PostHog), data-retention and deletion (`/account/delete`), encryption at rest, and the Common Sense rating |

### 4d. SSO and rostering platforms

| Platform | Reality in 2026 | Cost | Effort | Verdict |
|---|---|---|---|---|
| **Clever Library** | **Clever no longer offers new Library integrations**, contrary to older guides. New integrations must be SSO plus Rostering under a Clever Complete agreement ([Clever dev docs](https://dev.clever.com/docs/district-sso-vs-library-sso), [integration types](https://dev.clever.com/docs/integration-types)) | Free to build, but contract-gated | L | **Not a near-term channel.** Revisit after district DPAs |
| **ClassLink LaunchPad** | Partner Portal supports OAuth, LTI or SAML SSO. Apps appear in the district App Library once added ([ClassLink](https://help.classlink.com/s/article/pp-add-manage-sso-connections), [App Library](https://help.classlink.com/s/article/lp-home-app-library)) | Unverified | M | After the first district DPA: build OIDC SSO so that district's LaunchPad has an IntelliPlan tile |
| **Canvas Apps (LTI 1.3)** | EduAppCenter is legacy LTI 1.1. The new Canvas Apps "Discover" listing requires becoming an **Instructure Partner**; there is a Canvas Certified badge ([Instructure community](https://community.canvaslms.com/t5/Canvas-Developers-Group/LTI-1-3-eduappcenter-and-keys-and-secrets/td-p/561123), [Instructure blog](https://www.instructure.com/resources/blog/boost-visibility-and-adoption-canvas-apps-experience)) | Partner fees unverified | L | **Defer.** The real Canvas friction (a per-school Developer Key) is already bypassed by the one-URL calendar feed (`ics_feed.py`) and personal tokens |
| **Google Classroom add-on / Workspace Marketplace** | Must pass Marketplace review; Google SSO required for teachers and students ([requirements](https://developers.google.com/workspace/classroom/add-ons/requirements), [review](https://developers.google.com/workspace/classroom/add-ons/developer-guides/review-process-overview#complete_oauth_verification)). License prerequisites for teachers are unverified | Free | L | Defer. A **Marketplace listing for the web app** (not an add-on) is cheaper and gives districts an "allowlist" button. Consider it after Google OAuth verification is complete. `google_unverified.html` is shown while `GOOGLE_OAUTH_UNVERIFIED` is set, and its own comments call the unverified-app warning "the single biggest drop-off point" in the Calendar flow. If that flag is still on in production, finishing verification is a conversion fix for every channel above |

---

## 5. App directories and stores

| Directory | Reach | Cost | Effort | First action |
|---|---|---|---|---|
| **Chrome Web Store** | The Canvas-helper category alone has 1–2M-user extensions (see top). Store search surfaces results for "Canvas," "StudentVUE," "Schoology." Chromebooks dominate K-12 | $5 one-time; first review takes 1–3 weeks ([pearpages](https://pearpages.com/blog/2026/07/19/publishing-chrome-extensions-what-it-takes-what-it-costs), [ctrlshiftcopy](https://www.ctrlshiftcopy.com/blog/publish-chrome-extension-to-web-store)) | S | Publish `extension/` now, named **"IntelliPlan: Canvas & StudentVUE Planner."** Screenshots should show the in-Canvas badge and the plan. After a few months of clean history, nominate for the **Featured badge** via one-stop support ([Google blog](https://blog.google/products-and-platforms/products/chrome/find-great-extensions-new-chrome-web-store-badges/), [extension.ninja](https://www.extension.ninja/blog/post/how-to-get-featured-extension-badge-chrome-web-store/)). **Compounding.** |
| **Microsoft Edge Add-ons** | Smaller | Free | S | Same package, same week |
| **Google Play** | Android students; store search | $25 one-time. **A new personal account must run a closed test with ≥12 testers for 14 days** before production ([Play Console Help](https://support.google.com/googleplay/android-developer/answer/14151465?hl=en)). An organization account is exempt | M (the packet is ready in `android-version/`) | Start the closed test in week 1. Recruit testers from the Discord, the ambassadors and r/alphaandbetausers |
| **Apple App Store** | 88% of US teens use iPhones ([MacRumors / Piper Sandler](https://www.macrumors.com/2025/04/09/teen-iphone-ownership-continues-to-soar/)) | $99/year plus a Mac | M | Follow `AppStore-Launch/README.md`. Until it ships, the webcal feed (competitors.md gap #4) is the iPhone bridge |
| **Product Hunt** | Featured launch: about 1,000–5,000 visitors and 10–150 signups. Non-featured: about 100–500 visitors ([shno.co](https://www.shno.co/marketing-statistics/product-hunt-launch-statistics)). The audience is builders, not students | Free | M (prep a list of 500+ supporters; the first 4 hours matter) | Launch once the Chrome extension and Play app are live, for a "now on every platform" story. The main value is the **backlink** and "Top product" badge for trust |
| **AlternativeTo** | ~2.84M visits per month. Product pages rank for "[X] alternative" ([launchdirectories](https://launchdirectories.com/directory/alternativeto)) | Free (the account must be 7 days old; approval takes days to a week) | S | List IntelliPlan as an alternative to **MyStudyLife, myHomework, Power Planner, Shovel, Motion, Quizlet, Tasks for Canvas.** Compounding intent traffic |
| **G2** | Buyer-side (districts), not students ([G2 education](https://www.g2.com/best-software-companies/top-education)) | Free listing | S | Claim a free profile for district credibility only. Low priority |
| **AI directories** (There's An AI For That, Futurepedia, etc.) | Unverified | Mostly free, some paid | S | Batch-submit with the same copy; mainly for backlinks |

---

## 6. SEO keyword clusters

Only one volume here is sourced: **"grade calculator" is about 550K searches per month (US)**. RogerHub ranks #1 for "final grade calculator" and gets about 70K visits per month from that keyword ([ahrefstop summary](https://ahrefstop.com/websites/rogerhub.com)). RogerHub's traffic swings seasonally: 764K visits in June 2026, down 54% from May ([Semrush](https://www.semrush.com/website/rogerhub.com/overview/)). Everything else below is **unverified**. Confirm in Google Search Console (IntelliPlan's own impressions) and Keyword Planner before building.

| Cluster | Example keywords | Existing page | What to build | Priority |
|---|---|---|---|---|
| **A. Platform-specific grade calculators** | "studentvue grade calculator", "studentvue what if grades", "canvas what-if grades", "schoology grade calculator", "powerschool grade calculator", "infinite campus grade calculator", "aeries grade calculator" | `/tools/grade-calculator`, `/tools/final-grade-calculator`, `/tools/test-grade-calculator` | One page per platform: a calculator that works with no login, plus "connect StudentVUE to fill this automatically," deep-linking into `/grademodel`. Demand evidence: several third-party StudentVUE and Aeries what-if tools exist ([Gradely](https://www.gradely.me/), [GradeVue 2](https://gradevue2.org/), [Super StudentVUE](https://chromewebstore.google.com/detail/super-studentvue/oadniffmkbpeokbdncbhbagbabadigfk?hl=en-US), [Aeries Grades+](https://chromewebstore.google.com/detail/aeries-grades+/edeaoofdafgcngkmimhfmhflcinfngap?hl=en)) | **1**. Ship before the December finals spike |
| **B. Generic calculators** | "grade calculator" (~550K), "final grade calculator", "what do I need on my final", "weighted grade calculator", "gpa calculator", "weighted gpa calculator", "high school gpa calculator" | `/tools/*` | Improve on-page UX, add FAQ schema, and interlink. Fight for positions 3–10; RogerHub and calculator.net own the top | 2 |
| **C. LMS how-tos** | "canvas calendar feed", "sync canvas to google calendar", "canvas to do list not showing", "how to check gpa on studentvue", "studentvue missing assignments", "google classroom to do list" | `/blog/how-to-use-canvas-with-a-study-planner`, `/blog/studentvue-study-planner` | Short, exact-answer guides, each ending with "or let IntelliPlan do it in one step." Many universities publish these guides, which shows demand ([Brown](https://ithelp.brown.edu/kb/articles/how-do-i-sync-canvas-with-my-google-calendar), [socialspy](https://socialspy.io/how-to-add-canvas-calendar-to-google-calendar/)) | 2 |
| **D. Alternatives and comparisons** | "mystudylife alternative", "myhomework alternative", "power planner alternative", "shovel app alternative", "motion for students", "tasks for canvas alternative", "free quizlet alternative" | 5 `/compare/*` pages | Add compare pages for Power Planner, Shovel, Motion and Tasks for Canvas. Tie to the competitors.md findings (for example MSL's paywalled rotations, Shovel's price) | 3 |
| **E. Planner category** | "study planner app", "ai study planner", "student planner app", "homework planner app", "canvas planner", "study schedule maker" | `/blog/best-*`, `/tools/study-schedule-maker` | Keep refreshed yearly ("2027"). Competitors like DormWay and powerplanner.net publish these listicles aggressively | 3 |
| **F. Seasonal exams** | "finals study schedule", "ap exam study schedule", "days until finals", "revision timetable" (UK) | `/tools/finals-countdown`, `/blog/ap-study-planner`, `/uk` | Update pages 6 weeks before each season: December finals, AP in May, GCSE and A-level in May–June | 3 |

**Mechanics.** Submit new pages through the existing IndexNow endpoint and Search Console. Keep calculators working without login and without JavaScript-only rendering, so the answer is in the HTML.

- **Cost:** $0.
- **Effort:** S per page.
- **This is the most compounding channel available.**

---

## 7. Partnerships

| Partner type | Why | First action | Effort |
|---|---|---|---|
| **Notion template ecosystem** | IntelliPlan already has two-way Notion sync. Notion has 100M+ users and a Campus Leaders program in 40+ countries ([Notion for Education](https://www.notion.com/product/notion-for-education), [X post](https://x.com/ImadeIyamu/status/1927999099493650471)) | Publish a free "Student Dashboard" Notion template whose task database IntelliPlan fills automatically. List it in the Notion template gallery and pitch it to Notion Campus Leaders | S–M |
| **Study Together Discord** (~1M members) | Live studying audience | Sponsor a finals-week event (see §2) | S |
| **AP communities (Fiveable, the AP Students Discord)** | Multiple-AP planning is IntelliPlan's sharpest use case | Offer a free co-branded "AP season planner" resource | M |
| **StudentVUE district communities** | StudentVUE users are underserved: the official app has no what-if, and third-party tools get shut down ([studentvues](https://studentvues.com/why-did-studentvue-remove-the-calculator/)) | Recruit one ambassador per large StudentVUE district (district URLs are visible in the DB) | M |
| **Tutoring and test-prep orgs (non-competing)** | They need students to show up prepared | Offer free parent-dashboard seats for their students | M |
| **Hack Club and teen builder communities** | The "built by a student" story; ambassador recruiting | Share a build write-up. Hack Club community size is unverified | S |
| **ADHD organizations (for example CHADD)** | Breakdown, Focus Shield and timeline features serve this group | Only after those features ship, and with no treatment claims | M |

---

## 8. Channel scorecard (summary)

| Channel | Scalable or compounding? | Reach (est.) | Cost | Effort | Rank |
|---|---|---|---|---|---|
| Chrome Web Store (+ Edge) | ✅ store search plus ratings | 1–2M-user category | $5 | S | 1 |
| Programmatic SEO (platform calculators, how-tos) | ✅ | 550K/month head term plus long tail | $0 | S per page | 2 |
| Creator and UGC ambassador program | 🟡 (winners can be re-used as ads) | 5–50K per video | $2–5K/month | M | 3 |
| School trust kit (NDPA, Common Sense, privacy packet) plus counselor outreach | ✅ (Exhibit E reuse) | School-wide adoption | ~$0 plus legal time | M | 4 |
| Directory sweep (AlternativeTo, Product Hunt, G2, AI directories) | ✅ backlinks and alternative-search traffic | 1–5K per launch; ongoing referrals | $0 | S–M | 5 |
| Google Play / App Store | ✅ | Large | $25 / $99 per year | M | Part of move 1 and later |
| Reddit | ❌ manual, with ban risk | Spiky | $0 | High | Supporting |
| Discord events | 🟡 | Thousands per event | Low | S | Supporting |
| Clever / ClassLink / LTI / Classroom add-on | ✅ but gated | District-wide | Contracts or partner fees | L | After first DPA |
| ASCA or conference booths | ❌ | ~5,000 counselors | $$$ (unverified) | M | Defer |

---

## 9. Prioritized 30-day plan (Oct 1–31, 2026): top 5 moves

These favor channels that keep paying after the work is done. Timing matters: **December finals** is the next demand spike for grade calculators and planning, and pages need about 4–8 weeks to index and rank.

### Move 1: Put IntelliPlan in the stores students already search (week 1, then continuing)
1. Register a Chrome Web Store developer account ($5). Publish `extension/`, named **"IntelliPlan: Canvas & StudentVUE Planner."** Include keyword-rich copy and 5 screenshots. Add the Edge Add-ons listing the same week.
2. Start the Google Play **closed test (at least 12 testers × 14 days)** now, so production access lands in late October. Recruit testers from the Discord, ambassadors and r/alphaandbetausers. The packet is in `android-version/`.
3. Add "Get the extension" and "Get it on Google Play" badges to `/`, `/download` and `/install`.
4. **KPI:** extension installs per week, and install → activated user.

*Why first:* Tasks for Canvas and BetterCampus show a 1–2M-user demand pool that IntelliPlan can't reach at all today.

### Move 2: Ship platform-specific calculator pages before finals (weeks 1–3)
1. Build `/tools/studentvue-grade-calculator`, `/tools/canvas-what-if-grades`, `/tools/schoology-grade-calculator` and `/tools/powerschool-grade-calculator`. Add Infinite Campus and Aeries if time allows.
   - Each works with **no login** and has FAQ schema.
   - Each ends with a one-click "auto-fill from my StudentVUE/Canvas," which leads to signup, then `/grademodel`.
2. Add 4 LMS how-to posts (cluster C) and 3 compare pages (Power Planner, Shovel, Tasks for Canvas).
3. Submit through IndexNow and Search Console, and interlink from the existing `/tools/*` pages.
4. **KPI:** Search Console impressions and clicks on the new pages; calculator → signup rate.

### Move 3: Turn `/ambassador` into a creator program (weeks 1–4)
1. Add cash **per-activated-referral** bounties, plus a flat per-video fee for creators, alongside the existing Pro-month tiers. Show both on `/ambassador` and track in `/ambassador/dashboard`.
2. Source 20 micro creators (10K–150K followers; AP, college Canvas, nursing, ADHD) via TikTok Creator Marketplace, Passionfroot and Collabstr. Budget about $3K. Send a 5-hook brief. Require the "app flash" format and a `/ref/<code>` link in the bio.
3. Post 3–5 short demos a week from IntelliPlan's own TikTok and Instagram, reusing whichever hooks win.
4. **KPI:** cost per activated user by creator. Double down on the top 3 creators in November.

### Move 4: Build the school trust kit, then do targeted counselor outreach (weeks 2–4)
1. Request a **Common Sense Privacy** evaluation (free).
2. Prepare the **SDPC National DPA** with a general **Exhibit E**, and ask one district where IntelliPlan already has users to sign it. Get it posted on the SDPC Registry.
3. Add a "Privacy packet" section to `schools.html` covering:
   - the NDPA status
   - subprocessors
   - encryption at rest (`secret_box.py`)
   - read-only scopes
   - deletion (`/account/delete`)
   - the COPPA flow (`/account/age`, `/parent/consent`)
4. Email counselors at the 25 schools with the most IntelliPlan users. Lead with Grade Pulse and missing-work alerts and the parent dashboard. Log each contact via `/api/school-outreach`.
5. **KPI:** district conversations, and the first signed NDPA. A single one unlocks Exhibit E reuse across that state's alliance.

### Move 5: Directory and launch sweep for backlinks and "alternative to" traffic (week 4)
1. List on **AlternativeTo** as an alternative to MyStudyLife, myHomework, Power Planner, Shovel, Motion, Quizlet and Tasks for Canvas. Create the account in week 1, because it must be 7 days old.
2. Claim a **G2** profile and submit to 3–5 AI directories.
3. Prepare a **Product Hunt** launch for early November, once the extension and Play app are live, for a "now on every platform" story. Build a supporter list in the Discord during October.
4. **KPI:** referring domains, and "alternative" referral traffic in analytics.

**Deliberately not in the 30 days:**
- Reddit campaigns: manual, and they don't compound. Do opportunistic helpful posting only.
- Clever integration: Library is closed to new integrations.
- LTI, the Canvas Partner program and Classroom add-ons: large effort, gated.
- 1EdTech certification: $2–10K.
- Conference booths.

Revisit these in Q1 2027, after a first district DPA and store traction.

### Data caveats
- **Subreddit sizes and creator follower counts** are third-party snapshots, and several are marked unverified. Reddit and most analytics sites were blocked in this session, and Reddit itself hides subscriber counts since September 2025.
- **Search volumes:** only "grade calculator" (~550K/month, via RogerHub's Ahrefs data) is sourced. All other keyword demand is inferred from the existence of competing tools and guides.
- **Reach and cost estimates** marked "est." are judgment calls, not measured.
