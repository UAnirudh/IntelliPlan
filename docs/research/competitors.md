# IntelliPlan competitor research: top 20 student and study planners

_Research date: 2026-09-30. Written from web search results (WebSearch). Reddit, and most review and stats sites, were blocked by this session's egress policy, so "what users say" comes from review aggregators, App Store snippets and third-party write-ups that quote Reddit, not from Reddit itself. Prices change often. Treat every number as "as reported by the linked source on or before the research date."_

## TL;DR

- **IntelliPlan already matches or beats most of the field on its core loop.** That loop is LMS import, prioritization, an auto-built plan, rescheduling, grade modeling and an AI tutor, all free. Shovel's headline "Time Cushion" feature is covered by `/api/schedule/feasibility` plus the Monte Carlo deadline odds. Motion's auto-rescheduling is covered by `/api/schedule/autopilot` and `/schedule/reflow`, and Motion charges about $19–29/month for it.
- **The gaps are in the *daily pull-back* layer, not in the planner.** These are the things that make a student open the app tomorrow without being asked:
  - grade and new-assignment alerts
  - plan-aware distraction blocking
  - a friend or accountability loop
  - home-screen presence (a live calendar feed and widgets)
  - a class timetable with rotating schedules
  - a "just start" task breakdown
- **The biggest problem is distribution, not features.** The two best-known Canvas helpers are Tasks for Canvas at 1M Chrome users and BetterCampus at about 2M ([canvascope](https://www.canvascope.org/compare/best-canvas-chrome-extensions), [chrome-stats](https://chrome-stats.com/d/tasks-for-canvas)). IntelliPlan's extension, native mobile app and Android packet are built, but nothing in the repo links to a Chrome Web Store, Play or App Store listing. See `distribution.md`.

---

## How the 20 were chosen

I started from the brief's candidate list and checked each one with search. I kept products that high-school or college students actually use to plan schoolwork, or to do the study and focus work that a planner schedules. I weighted student usage over general popularity.

**Cut, and why:**

| Candidate | Why cut |
|---|---|
| Clockwise | Shut down on 2026-03-27 after a Salesforce acquihire ([Doodle](https://doodle.com/en/clockwise-is-shutting-down-what-to-do-next-in-2026/), [usecarly](https://www.usecarly.com/blog/clockwise-shut-down/)). |
| Sunsama, Akiflow, Morgen | Priced for professionals: Sunsama about $20/month annual, Akiflow about $19/month annual, Morgen about $15/month ([Morgen](https://www.morgen.so/blog-posts/sunsama-vs-akiflow)). Negligible student share. Motion and Reclaim represent the category below. |
| Trevor AI | Good cheap time-blocker (free, Pro about $5/month, [usecarly](https://www.usecarly.com/blog/trevor-ai/)), but no student-specific pull. Its idea is covered by Motion and Reclaim. |
| Saner.ai | Aimed at adult ADHD knowledge workers ([Saner](https://www.saner.ai/blogs/ai-for-adhd)). |
| Things 3, Microsoft To Do | Generic task managers with no student features. Todoist and TickTick represent the category. |
| Egenda | Loved when it was active (4.7★ iOS), but reviews call it "very outdated with no online sync… no grades, no widgets" ([powerplanner.net](https://powerplanner.net/best-homework-planner-apps), [App Store](https://apps.apple.com/us/app/egenda-school-planner-assistant/id1142359153)). Replaced by School Planner, which is far larger. |
| iStudiez Pro | Legacy paid app ($2.99 mobile, $9.99 Mac, [search summary](https://istudiez-pro.macupdate.com/)). Little current momentum. |
| Brainly | A homework Q&A site, not a planner. |
| StudyFetch, Gizmo, Coconote | Real threats in "AI study content," but Turbo AI and Knowt represent that category. They are noted below where relevant: StudyFetch has 6M+ students ([tooldirectory](https://tooldirectory.ai/tools/studyfetch)), Gizmo has 1M+ public decks ([App Store](https://apps.apple.com/us/app/gizmo-ai-tutor/id1610516671)), and Coconote was acquired by Quizlet ([stormy.ai](https://stormy.ai/blog/coconote-ugc-playbook-app-growth-2026)). |

**Emerging direct competitors to watch.** These are too small or unverified to rank, but they are going after IntelliPlan's exact pitch:

- **DormWay:** free, AI, syncs Canvas, Blackboard and Moodle, reads syllabi ([dormway](https://dormway.app/blog/best-ai-study-planner-apps-2026)).
- **Luna.List:** Canvas-synced to-do list ([App Store](https://apps.apple.com/py/app/luna-list-to-do-for-students/id6744590356)).
- **StudySync:** open source, Canvas to Apple Calendar plus iOS widgets ([GitHub](https://github.com/aiden0rchad/StudySync)).
- **StudentVUE grade tools:** Gradely, GradeVue 2, Gradewave, Super StudentVUE ([gradely](https://www.gradely.me/), [gradewave](https://gradewave.org/), [gradevue2](https://gradevue2.org/)).

---

## The final 20

**Legend for "IntelliPlan equivalent":**

- ✅ = equal or better
- 🟡 = partial
- ❌ = missing

Citations point to routes in `App.py` unless another file is named.

### A. Student planners

#### 1. MyStudyLife
- **Target user:** High school and college students with fixed class timetables. Strong in the UK and in schools with rotating schedules.
- **Pricing:** Free with ads. MSL+ is $4.99/month or $29.99/year ([studytoolguide](https://studytoolguide.com/comparisons/is-mystudylife-still-worth-using-2026-red-flags-alternatives), [f6s](https://www.f6s.com/software/mystudylife)).
- **Platforms:** iOS, Android, web.
- **Best at:**
  1. Rotating timetables (Day A/B, Week 1/2), described as "best-in-class" and "the app's strongest feature."
  2. Class, exam and task views in one place.
- **Complaints:**
  - The 2025–26 redesign got mixed reviews.
  - Ads were added to the free tier.
  - Rotations, widgets, "AI Schedule Scan" (photo to timetable), subtasks and even task *types* are now paywalled ([studytoolguide](https://studytoolguide.com/comparisons/is-mystudylife-still-worth-using-2026-red-flags-alternatives), [mindomax](https://www.mindomax.com/best-study-apps-for-students)).
  - No LMS sync.
- **IntelliPlan equivalent:** 🟡
  - Manual classes store only `period` and `room` as strings (`ManualCourse`, `App.py` ~L1024; `/api/classes/manual`). There are no meeting times and no rotations.
  - Tests are tracked via `/tests` and `/api/tests`.
  - LMS sync is far ahead of MSL.
  - Comparison page already exists: `/compare/intelliplan-vs-mystudylife`.

#### 2. myHomework Student Planner
- **Target user:** Middle and high schoolers who want a simple homework list.
- **Pricing:** Free with ads. Premium is $4.99/year ([search summary](https://thinkassist.app/blog/myhomework-student-planner)).
- **Platforms:** iOS, Android, Mac, web.
- **Best at:**
  1. A dead-simple homework list with reliable reminders.
  2. Its **widget**, which users call "better than competitors" ([justuseapp](https://justuseapp.com/en/app/303490844/myhomework-student-planner/reviews)).
  - Rated 4.6★ with 50k+ reviews.
- **Complaints:**
  - Phases of crashes.
  - Only one reminder per assignment.
  - No grades or GPA.
  - No Canvas or Blackboard auto-sync.
  - Basic UI ([thinkassist](https://thinkassist.app/blog/myhomework-student-planner)).
- **IntelliPlan equivalent:** ✅ for the list and reminders, ❌ for widgets.
  - List: `/dashboard`, `/tasks/unified`.
  - Reminders: `/push/subscribe`, `intelliplan/notifications/events.py`.
  - Widgets: none in `mobile/`.
  - Comparison page exists: `/compare/intelliplan-vs-myhomework`.

#### 3. Power Planner
- **Target user:** Grade-conscious high school and college students.
- **Pricing:** Free and ad-free. A one-time purchase of about $1.99 unlocks unlimited semesters and more than 5 grades per class ([educationalappstore](https://www.educationalappstore.com/app/power-planner), [mindomax](https://www.mindomax.com/best-study-planner-apps)).
- **Platforms:** iOS, Android, Windows, web, with offline support.
- **Best at:**
  1. The grade tracker and "what-if" feature ("grades you need to reach certain GPAs").
  2. A generous free tier plus offline use on 4 platforms. Rated 4.5★ with 12k+ reviews.
- **Complaints:**
  - The free tier is limited to 1 semester.
  - No LMS sync.
  - No AI.
- **IntelliPlan equivalent:** ✅ for grades, 🟡 for offline.
  - Grades: `/grademodel`, `/api/grademodel/simulate`, `/api/grademodel/feasibility`, `/api/grades/predict`, `/tools/gpa-calculator`.
  - Offline: `static/sw.js` and the desktop app (`desktop/`). Mobile offline is not verified.

#### 4. School Planner (Andrea Dal Cin, Android-first)
- **Target user:** Android high schoolers worldwide.
- **Pricing:** Free with IAP (details not verified).
- **Platforms:** Android (plus iOS per [schoolplanner.me](https://schoolplanner.me/)).
- **Best at:**
  1. Timetable plus homework plus grades in one free app, at huge scale: 17M+ downloads and 4.33★ from about 180k reviews ([search summary of Play/AppBrain](https://www.appbrain.com/app/school-planner/daldev.android.gradehelper)).
- **Complaints:** Not verified. Search returned no review detail. It is a manual-entry app.
- **IntelliPlan equivalent:** 🟡. Same as MyStudyLife: timetable is missing, grades and homework are covered.

#### 5. Shovel
- **Target user:** College students (from the "How to Study in College" creator).
- **Pricing:** Paid only. $9.79/month or about $35–39/year after a 7-day trial ([shovelapp pricing](https://shovelapp.io/pricing/), [dormway](https://dormway.app/blog/best-ai-study-planner-apps-2026)).
- **Platforms:** iOS, Android, web.
- **Best at:**
  1. The **Time Cushion**: "is this schedule even possible?" It shows live slack between available hours and needed hours.
  2. Semester-wide planning. It now syncs Canvas, Brightspace, Moodle and Google Classroom ([shovel blog](https://shovelapp.io/blog/shovel-the-only-study-planner-that-creates-actionable-study-plans-from-canvas-brightspace-moodle-and-google-classroom/)). It added streaks in June 2026.
- **Complaints:**
  - Price.
  - No free tier.
- **IntelliPlan equivalent:** ✅ and arguably better.
  - `/api/schedule/feasibility` (`intelliplan/api/next_action.py`).
  - Per-assignment on-time probability from simulating the plan (README, "Follow-Through Engine").
  - `/api/schedule/forecast`.
  - Streaks: `streak_engine.py`.
  - All of it free, with StudentVUE and Schoology, which Shovel lacks.

#### 6. LMS built-ins: Canvas Student To-Do and Google Classroom To-Do (with Google Calendar and Tasks)
- **Target user:** Every student by default. This is the true "do nothing" competitor.
- **Pricing:** Free.
- **Platforms:** Web and mobile.
- **Best at:**
  1. Zero setup. Classroom due dates appear in Google Calendar automatically.
  2. The Canvas Calendar Feed can be pasted into any calendar ([search summary](https://sites.google.com/view/classrooms-workspace/tools-features/calendar-plan), [Brown IT](https://ithelp.brown.edu/kb/articles/how-do-i-sync-canvas-with-my-google-calendar)).
- **Complaints:**
  - The Canvas mobile To-Do shows stale items and can't mark items done after updates ([Penn State IT](https://www.it.psu.edu/news/canvas/attention-students-mobile-to-do-list)).
  - Canvas "shows too much information… minimal ways to clean up" ([medium](https://iryze21.medium.com/about-the-project-274b4a1406e0)).
  - Imported feeds are read-only.
  - Google Tasks has "no native Google Classroom sync, no gradebook" ([tasksboard](https://tasksboard.com/blog/google-tasks-for-students-study-planner)).
  - Nothing tells you *when* to do the work.
- **IntelliPlan equivalent:** ✅. It uses these as inputs:
  - `canvas_oauth.py`
  - `ics_feed.py`, the one-URL Canvas feed import at `/login/calendar-feed`
  - `/api/lms/connect/<provider>` for Google Classroom

#### 7. Tasks for Canvas / BetterCampus (Chrome extension)
- **Target user:** Canvas students on Chromebooks and laptops.
- **Pricing:** Free (the extension).
- **Platforms:** Chrome. Also works on Blackboard and D2L.
- **Best at:**
  1. It lives *inside* Canvas. It turns the dashboard sidebar into a to-do list with per-course progress rings, streaks and Gradescope items.
  2. Scale: **1,000,000 users** at 4.6★ (Tasks for Canvas), and BetterCampus reports about 2M ([canvascope](https://www.canvascope.org/compare/best-canvas-chrome-extensions), [chrome-stats](https://chrome-stats.com/d/tasks-for-canvas)).
- **Complaints:** Not verified. It stays Canvas-only and does not plan or schedule.
- **IntelliPlan equivalent:** 🟡.
  - `extension/` injects into Canvas and StudentVUE pages (`content.js`, `scrapers/`) and shows a badge count.
  - It is **not published** on the Chrome Web Store: no store link found in `Main_Project/templates/`, and the repo ships `IntelliPlan-Extension-V.*.zip`.
  - This is the single most important distribution gap (see `distribution.md`).

### B. General-purpose task and time tools

#### 8. Notion (+ Notion Calendar)
- **Target user:** College students who like to customize everything.
- **Pricing:** Free. Education Plus is free for students with a WHED-listed college email, **not for K–12**. The AI add-on is 50% off for 12 months ([Notion](https://www.notion.com/product/notion-for-education), [costbench](https://costbench.com/software/project-management/notion/discounts/)).
- **Platforms:** All.
- **Best at:**
  1. Infinite flexibility, with a huge template ecosystem for student dashboards.
  2. Notes and planning in one place. Notion has 100M+ users ([super.so](https://super.so/blog/notion-stats)).
- **Complaints:**
  - Weeks of setup.
  - Slow on mobile, especially Android.
  - Unreliable offline: "If my campus Wi-Fi drops, accessing my notes is a headache" ([fibery](https://fibery.io/openion/notion-2/mobile-app-performance-concerns-lag-sync-issues-and-slow-load-times-244843), [unanswered](https://unanswered.io/guide/notion-limitations-and-drawbacks)).
  - No automatic LMS import.
- **IntelliPlan equivalent:** ✅ as a complement.
  - Two-way Notion task sync: `/notion/*` routes, `notion_helper.py`.
  - Comparison page: `/compare/intelliplan-vs-notion`.

#### 9. Todoist
- **Target user:** Productivity-minded college students.
- **Pricing:** Free. Pro is $7/month or $60/year after a December 2025 price increase. The student discount was removed years ago ([ellieplanner](https://ellieplanner.com/productivity-copilot/todoist-pricing), [morgen](https://www.morgen.so/blog-posts/todoist-pricing)).
- **Platforms:** All.
- **Best at:**
  1. **Natural-language quick add**, for example "Submit report every second Thursday" ([dupple](https://dupple.com/reviews/todoist)).
  2. Fast, cross-platform capture. It reports 40–50M users.
- **Complaints:**
  - The monthly price hike "damaged trust."
  - The free plan "no longer competes."
  - No student or LMS features.
- **IntelliPlan equivalent:** 🟡.
  - NL extraction exists: `/api/tasks/extract`, `/api/import/smart_paste`, `/extractor`.
  - Manual tasks: `/tasks/manual/create`.
  - Missing: a global, instant quick-add (a keyboard palette, extension omnibox or share sheet).

#### 10. TickTick
- **Target user:** Students who want tasks, a Pomodoro timer and habits in one app.
- **Pricing:** Free with limits (9 lists × 99 tasks). Premium is about $35.99/year. Some sources say $49.99, with a 25% education discount ([aitoolpick](https://aitoolpick.org/blog/ticktick-pricing-2026/), [checkthat](https://checkthat.ai/brands/ticktick/pricing)).
- **Platforms:** All.
- **Best at:**
  1. An all-in-one bundle: tasks, calendar, Pomodoro with per-task focus stats, a habit tracker and an Eisenhower matrix.
  2. Low price for what you get.
- **Complaints:** No school integration. Generic.
- **IntelliPlan equivalent:** 🟡.
  - Focus timer: `/focus`, `/active`, `intelliplan/api/active.py`.
  - Priority matrix: `/priority`.
  - Stats: `/my-stats`.
  - No habit tracker, which is deliberately not recommended (see the gap table).

#### 11. Motion
- **Target user:** Professionals. Also marketed to students and to people with ADHD.
- **Pricing:** No free plan. $19/month annual or $29/month. Students get 25–50% off. Reports of annual-only billing and surprise trial charges ([ellieplanner](https://ellieplanner.com/productivity-copilot/motion-app-pricing), [toolguidehq](https://toolguidehq.com/is-motion-worth-it-for-college-students-2026/)).
- **Platforms:** Web, desktop, mobile.
- **Best at:**
  1. Fully automatic scheduling of tasks into the calendar, re-planned continuously.
  2. It removes the "when should I do this" decision.
- **Complaints:**
  - Overpacked days "without capacity visualization."
  - Moves things unexpectedly.
  - For task-initiation paralysis, "Motion will reschedule that task repeatedly without ever helping you start it."
  - Feature bloat, pricing confusion, support issues ([mutra](https://mutra.app/compare/pricing/motion-app/), [hirekai](https://hirekai.ai/blog/motion-app-review)).
- **IntelliPlan equivalent:** ✅, and the design is better. It shows the consequence before applying a change:
  - `/api/schedule/autopilot` (`intelliplan/intelligence/autopilot.py`)
  - `/api/schedule/override` and `/override/apply`
  - `/schedule/reflow`
  - `/calendar/free-slot`
  - Capacity warnings: `PLAN_OVERLOADED` in `intelliplan/notifications/events.py`

#### 12. Reclaim.ai
- **Target user:** Knowledge workers, some grad students.
- **Pricing:** The free Lite plan allows 1 habit, 1 calendar and a 1-week horizon. Paid plans are about $8–15 per seat per month; figures vary by source ([morgen](https://www.morgen.so/blog-posts/reclaim-pricing), [get-alfred](https://get-alfred.ai/blog/reclaim-pricing)).
- **Platforms:** Web (Google and Outlook calendars).
- **Best at:**
  1. "Habits": protected recurring blocks that auto-defend themselves when meetings move.
- **Complaints:** Built for meetings. The free tier is thin for students.
- **IntelliPlan equivalent:** 🟡.
  - Plan blocks reflow around calendar busy time: `busy_by_date` in `/api/schedule/feasibility`, `intelliplan/intelligence/rescheduling.py`.
  - Google and Outlook calendars: `/oauth/google`, `/oauth/outlook`.
  - No "defended recurring block" concept. Not needed; see the gap table.

#### 13. Structured
- **Target user:** Visual planners and ADHD users, many of them students.
- **Pricing:** Free. Pro is about $2.99–3.99/month, $19.99/year or $64.99 lifetime ([calmevo](https://calmevo.com/structured-app-review/), [saner](https://blog.saner.ai/best-adhd-planners/)).
- **Platforms:** Apple platforms, Android, web.
- **Best at:**
  1. A **visual vertical timeline** where blocks are sized to their duration, which helps with time blindness.
  2. New in 2026: an AI "brain dump → schedule" feature.
  - Scale: 7–9M downloads and about 1.5M active users ([designli](https://designli.co/blog/how-the-structured-app-achieved-millions-of-downloads-using-behavioral-design), [structured.app](https://structured.app/)).
- **Complaints:** Not verified in detail. It is manual-entry for schoolwork.
- **IntelliPlan equivalent:** 🟡. `/scheduler` shows blocks, but there is no dedicated time-proportional "Today" timeline with a live "now" line.

#### 14. Tiimo
- **Target user:** Neurodivergent (ADHD and autistic) teens and adults.
- **Pricing:** Limited free tier. Pro is $7.99/month or $79.99/year. Family plan is $119.99/year ([lifestack](https://lifestack.ai/blog/tiimo-pricing)).
- **Platforms:** iOS, Android, web (web requires Pro).
- **Best at:**
  1. Gentle visual planning with icons and a circular timer.
  2. **AI task breakdown**. Tiimo was **iPhone App of the Year 2025** ([lifestack](https://lifestack.ai/blog/tiimo-pricing)).
- **Complaints:** Price. The free tier has no AI and no focus timer.
- **IntelliPlan equivalent:** ❌ for step breakdown. 🟡 for the visual day.

#### 15. Goblin Tools (Magic ToDo)
- **Target user:** ADHD and executive-dysfunction users, including many students.
- **Pricing:** The website is free with no account. Mobile apps are about $3.99 one-time ([saner review](https://blog.saner.ai/goblin-tools-review/), [focushack](https://www.focushack.io/reviews/goblin-tools-adhd-review/)).
- **Platforms:** Web, iOS, Android.
- **Best at:**
  1. Magic ToDo breaks any task into steps, with a **"spiciness" slider** for granularity. Users describe getting "a list you can actually look at without anxiety."
- **Complaints:** "Solves *what* should I do but has no timer, no reminders, no scheduling to help you actually follow through."
- **IntelliPlan equivalent:** ❌.
  - The scheduler splits work into sittings (`scheduler_engine.py`, and `subtask_count` in `intelliplan/services/scheduling.py`).
  - There is no user-facing step checklist.
  - Goblin's weakness, "no scheduling," is exactly IntelliPlan's strength.

### C. Study-content tools (flashcards, notes, AI tutors)

#### 16. Quizlet
- **Target user:** Everyone. "2 of 3 US high schoolers and 1 in 2 college students use Quizlet every month," with 60M+ monthly active users ([search summary citing Quizlet](https://time.com/collection/time100-most-influential-companies/2026/quizlet/)).
- **Pricing:** Plus is $35.99/year or $7.99/month. Plus Unlimited is $44.99/year ([myengineeringbuddy](https://www.myengineeringbuddy.com/blog/quizlet-reviews-alternatives-pricing-offerings/)).
- **Platforms:** All.
- **Best at:**
  1. The **network of shared sets**: whatever your class is studying, someone has made the set already.
  2. Learn mode. It asks for a test date and builds an adaptive plan ([Quizlet blog](https://quizlet.com/blog/introducing-the-new-quizlet-learn)).
- **Complaints:**
  - A paywall creep. Learn and practice tests are now Plus-only, and free users are capped.
  - 1.4/5 on Trustpilot, driven by paywall anger ([wordsonrepeat](https://wordsonrepeat.com/blog/quizlet-paywall-2026-free-alternatives), [pawebpage](https://pawebpage.com/3269/opinions/quizlets-paywall-proves-that-students-are-its-last-priority/)).
- **IntelliPlan equivalent:** 🟡.
  - Flashcards with SRS: `flashcards/`, `/api/flashcards/*`.
  - **Quizlet and Anki import**: `flashcards/importers.py`.
  - AI generation from notes: `/study/generate`.
  - AP sets: `/library`.
  - No shared or public deck network.
  - Comparison page: `/compare/intelliplan-vs-quizlet`.

#### 17. Knowt
- **Target user:** High school (AP) and college students leaving Quizlet.
- **Pricing:** Free core. Ultra ranges from about $9.99/month annual up to $24.99/month; sources disagree ([makeheadway](https://makeheadway.com/blog/knowt-review/), [ailistingtool](https://ailistingtool.com/blog/knowt-ai-review-features-pricing-study-guide)).
- **Platforms:** Web, iOS, Android, plus a Chrome extension for Canvas, Moodle and Classroom.
- **Best at:**
  1. A free Learn mode and practice tests with one-click Quizlet import. This is how it reached 5M+ users.
  2. The AP Exam Hub.
- **Complaints:**
  - "Gradually moving previously free features behind the Ultra paywall, contradicting their 'free Quizlet alternative' marketing" ([nibomo](https://nibomo.com/blog/free-quizlet-alternative/), [studygenie](https://studygenie.io/blog/knowt-vs-quizlet)).
- **IntelliPlan equivalent:** 🟡. Same as Quizlet, plus the AP library at `/library`. IntelliPlan's "free forever, no locked planner features" (`pricing.html`) is a credible contrast to Knowt's paywall drift.
- **Distribution note:** Knowt pays **teacher ambassadors $1,000 up front** and recruits paid college ambassadors ([Knowt Knights](https://knowt.com/teachers/knowt-knights-teacher-ambassadors), [Polymer job post](https://jobs.polymer.co/knowt-inc/38258)).

#### 18. Turbo AI (TurboLearn)
- **Target user:** College students who record lectures.
- **Pricing:** Very limited free tier. $9.99/month annual or $19.99/month ([tldv](https://tldv.io/blog/turbo-ai/), [hyscaler](https://hyscaler.com/insights/turbolearn-ai-pricing-reviews-features/)).
- **Platforms:** Web, mobile.
- **Best at:**
  1. Lecture, PDF or video in; notes, flashcards and quizzes out. 5M+ users across 5,000+ colleges.
- **Complaints:**
  - Reddit calls it a "cheap GPT wrapper."
  - The free plan is stingy.
  - Mixed value-for-money ([tldv](https://tldv.io/blog/turbo-ai/)).
- **IntelliPlan equivalent:** 🟡.
  - Lesson library: `/lessons`, `/api/lessons`.
  - Transcription: `/study/transcribe`.
  - YouTube ingestion: `/study/youtube`.
  - PDF extraction: `/study/extract-pdf`.
  - Note to study set: `/notes/<id>/study`.
  - Upload-based, with no live in-class capture.
  - Comparison page: `/compare/intelliplan-vs-turbo-ai`.
- **Distribution note:** Turbo grew with a 100+ person ambassador and UGC program: about 4M users, about $300k MRR, about 200k monthly downloads ([stormy.ai](https://stormy.ai/blog/coconote-ugc-playbook-app-growth-2026), [superscale](https://superscale.ai/learn/tiktok-ugc-strategy-how-to-go-viral-for-your-app-in-2025/)). These are third-party figures and unaudited.

### D. Focus and distraction

#### 19. Forest
- **Target user:** Students who get distracted by their phone.
- **Pricing:** Paid one-time on iOS, free with IAP on Android. A new Forest Plus subscription is reportedly $5.99/month or $32–36/year ([calmevo](https://calmevo.com/forest-app-review/)).
- **Platforms:** iOS, Android, Chrome.
- **Best at:**
  1. A gamified focus timer: a tree dies if you leave the app.
  2. **Real trees planted** through Trees for the Future.
  - Scale: 60M downloads and 2M+ paying users ([forestapp.cc](https://www.forestapp.cc/), [medium](https://medium.com/design-bootcamp/how-a-top-rated-productivity-app-forest-uses-gamification-to-retain-users-9345f6867a2d)).
- **Complaints:** Anger at a subscription being added to a paid app.
- **IntelliPlan equivalent:** 🟡.
  - Pet and streak gamification: `pet_engine.py`, `/pet`, `/api/pet/*`, `streak_engine.py`.
  - Focus sessions: `/active`.
  - No "plant together" or friend mechanic.

#### 20. Opal
- **Target user:** Teens and adults who want to cut screen time.
- **Pricing:** Pro is about $99.99/year or $19.99/month, with a $399 lifetime option and 50% off for verified students ([autonomous](https://www.autonomous.ai/ourblog/opal-app-review), [makeheadway](https://makeheadway.com/blog/opal-app-review/)).
- **Platforms:** iOS (Screen Time API), Android, desktop.
- **Best at:**
  1. Real **app and site blocking** on schedules, including "Deep Focus" sessions.
  - Scale: 1M+ daily active users and about $10M ARR ([RevenueCat](https://www.revenuecat.com/blog/growth/kenneth-schlenker-sub-club-podcast-2026)).
- **Complaints:**
  - Price shock.
  - Trial-to-charge surprise.
  - A "nearly useless" free tier.
  - Aggressive upsells.
  - The blocking overlaps with free iOS Screen Time.
- **IntelliPlan equivalent:** 🟡.
  - `/api/focus/enforcement` has modes `off`, `alarm`, `takeover` and `stakes` (`FOCUS_ENFORCEMENT_MODES`, `App.py` ~L17744).
  - `/active` has an optional on-device camera focus check.
  - **It detects distraction but does not block anything.**

---

## Where IntelliPlan already leads (don't spend effort here)

| Capability | Best competitor | IntelliPlan |
|---|---|---|
| Multi-LMS import, including K-12 SIS | Shovel (4 LMSs), DormWay | Canvas, StudentVUE, Schoology, Google Classroom, Blackboard, Moodle, D2L and ICS feeds (`integrations_catalog.py`) |
| "Is my plan feasible?" | Shovel Time Cushion | `/api/schedule/feasibility` plus on-time probabilities |
| Auto-reschedule | Motion ($19–29/month) | `/api/schedule/autopilot`, `/schedule/reflow`, with a preview |
| What-if grades | Power Planner, GradeView | `/grademodel`, `/api/grades/predict`, `/tools/*-calculator` |
| AI tutor with memory | StudyFetch Spark.E, Knowt Kai (paid) | Plani with an adaptive learner model (`adaptive_tutor/`), free |
| Parent view | Almost none | `/parent_dashboard`, `/api/roles/*` |
| Price | Most charge $30–100+/year | Free. Pro at $5/month only lifts AI caps (`pricing.html`) |

---

## Ranked feature gap table

**Scoring:** Score = (Retention impact 1–5 × Evidence 1–5) ÷ Effort, where S = 1, M = 2, L = 3, and S/M = 1.5.

- *Retention impact* is my judgment of how much the gap drives daily or weekly return visits for students.
- *Evidence* reflects the size and quality of the demand signal cited.
- *Effort* is for this Flask plus Expo codebase, assuming the existing modules named below are reused.

| Rank | Gap (best-in-class competitor) | User pain | Evidence of demand | Effort | R × E ÷ Eff |
|---|---|---|---|---|---|
| 1 | **Grade-posted and new-assignment alerts with "what it means"** (StudentVUE grade tools, Power Planner) | "I find out about a bad grade or a new assignment days later, then panic." | Four or more third-party StudentVUE what-if and grade tools exist (Gradely, GradeVue 2, Gradewave, Super StudentVUE); students search "why did StudentVUE remove the calculator" ([studentvues](https://studentvues.com/why-did-studentvue-remove-the-calculator/)); Power Planner's what-if is its most-praised feature | **S** | 5×4÷1 = **20** |
| 2 | **"Break it down" plus a 5-minute start** (Goblin Tools, Tiimo AI) | Paralysis on big assignments, which Motion can't fix ("reschedules repeatedly without helping you start") | Goblin Tools' popularity; Tiimo was iPhone App of the Year 2025 with AI breakdown; Structured added AI brain-dump in 2026 | **S** | 4×4÷1 = **16** |
| 3 | **Plan-aware distraction blocking** (Opal, Forest) | "I sit down to study and end up on YouTube." | Opal has 1M+ DAU and $10M ARR at $100/year; Forest has 60M downloads | **S/M** | 4×5÷1.5 = **13.3** |
| 4 | **Live, subscribable calendar feed of the *plan*** (Canvas Calendar Feed, StudySync) | The plan lives in IntelliPlan, but students live in the Apple Calendar that came with their phone | 88% of US teens own an iPhone ([Piper Sandler via MacRumors](https://www.macrumors.com/2025/04/09/teen-iphone-ownership-continues-to-soar/)); abundant "Canvas to Google Calendar" guides ([socialspy](https://socialspy.io/how-to-add-canvas-calendar-to-google-calendar/), [Brown](https://ithelp.brown.edu/kb/articles/how-do-i-sync-canvas-with-my-google-calendar)); StudySync exists just to push Canvas into Apple Calendar | **S** | 3×4÷1 = **12** |
| 5 | **Friend streaks and study buddies** (Forest "plant together", Gizmo leaderboards, Study Together Discord) | Studying alone, with nothing but internal motivation | Duolingo: users with at least one Friend Streak are **22% more likely** to complete the daily lesson ([Duolingo blog](https://blog.duolingo.com/product-lessons-friend-streak/)); the Study Together Discord has about 1.06M members ([top.gg](https://top.gg/discord/servers/542882462976860160)) | **M** | 5×4÷2 = **10** |
| 6 | **Instant quick-add everywhere** (Todoist) | A teacher says "quiz Friday" and the student has 3 seconds to capture it | NL input is the most-praised Todoist feature ([dupple](https://dupple.com/reviews/todoist)) | **S** | 3×3÷1 = **9** |
| 7 | **Class timetable with rotating schedules** (MyStudyLife, School Planner, Power Planner) | "What's next period, which room, is it an A or B day?" The scheduler also can't know when a student is in class | MSL's #1 feature, now paywalled; School Planner has 17M downloads on essentially this feature | **M** | 4×4÷2 = **8** |
| 8 | **Visual "Today" timeline** (Structured, Tiimo) | Time blindness: a list doesn't show that the day is already full | Structured has 7–9M downloads and about 1.5M active users; Tiimo won App of the Year | **S/M** | 3×4÷1.5 = **8** |
| 9 | **Home-screen and lock-screen widgets** (myHomework, MSL+, StudySync) | Out of sight, out of mind: the app is only opened when remembered | myHomework's widget is its most-praised feature; MSL paywalls widgets | **L** (store apps plus native widget targets) | 5×4÷3 = **6.7** |
| 10 | **Classmate-shared decks for the same course or test** (Quizlet, Knowt, Gizmo) | Every student makes the same deck from scratch | Quizlet's 60M MAU are built on shared sets; Gizmo advertises 1M+ public decks | **M** | 3×4÷2 = **6** |
| — | *Not worth closing (see below)* | | | | |
| 11 | Live in-class lecture capture (Turbo, StudyFetch, Coconote) | Note-taking during lectures | Strong: Turbo 5M, StudyFetch 6M, Coconote acquired by Quizlet | M | 2×5÷2 = 5 |
| 12 | Public deck marketplace (Quizlet) | Discovery | Strong, but incumbents own it | L | 2×5÷3 = 3.3 |
| 13 | Real-tree or charity tie-in (Forest) | Meaning behind focus | Forest's growth story | M | 2×3÷2 = 3 |
| 14 | Habit tracker (TickTick) | Non-school habits | Moderate | S | 1×3÷1 = 3 |
| 15 | Meeting links and "defended" recurring blocks (Reclaim, Motion) | Meetings | Weak for students | M | 1×2÷2 = 1 |

Ranks 7 and 8 tie on score. I put the timetable first because it also feeds rank 8 (timeline) and rank 9 (widgets), and it makes every generated plan avoid class hours.

### Specs: how IntelliPlan can do each one *better*, not just match

**1. Grade Pulse: alerts that come with a next step** (S)
- **What it does.** On every LMS sync, plus a scheduled sync via the existing `/cron/*` pattern, diff the gradebook and the assignment list.
- **Three new event kinds** in `intelliplan/notifications/events.py`, delivered through the existing `dispatcher.py` (Web Push, Expo push, SMS, email):
  - `GRADE_POSTED`
  - `ASSIGNMENT_POSTED`
  - `MISSING_FLAGGED`
- **Better than the StudentVUE grade tools.** They only *show* the grade. Grade Pulse *acts on it*, with a message like: "Chem, Unit 3 Test: 78%. Course grade 88.4 → 86.3 (B+). To finish with an A−, you need 91% on the final. [Model it]."
  - The "you need" number comes from `/api/grademodel/feasibility`.
  - A new assignment is slotted into the plan straight away (`/schedule/reflow`), and the alert says where it went.
- **Hooks already in place:**
  - `learning_graph_glue.py` → `_learning_graph_on_grade_changed`, already fired with `old_pct` and `new_pct`.
  - Notification preferences: `/api/notifications/preferences`.
- **Extras:**
  - An opt-in parent digest through the existing role scopes (`/api/roles/links/<id>/scopes`).
  - Batching and quiet hours, so a teacher posting 30 grades at 11pm sends one message.
- **Guardrail.** Default to "grade dropped more than X" and "new assignment," not every score, to avoid notification fatigue.

**2. Break it down + "Just 5 minutes"** (S)
- **Where it lives.** A button on every assignment.
- **Grounded in the real assignment.** The LLM gets the actual assignment description or rubric (`/assignment/description`) and the course. Goblin Tools only sees what you type.
- **Granularity slider**, like Goblin's "spiciness" setting.
- **Time estimates.** Each step gets a minute estimate, calibrated by the student's own history (`/feedback/predict-time`).
- **Steps are scheduled.** Steps become sub-blocks of the plan (`subtask_count` already feeds the splitter in `intelliplan/services/scheduling.py`).
- **"Start the 5-minute version"** launches `/active` on step 1, directly attacking initiation paralysis, which is Motion's blind spot.
- **Progress counts.** Ticking steps updates `/schedule/progress`, so partial work counts toward the plan.

**3. Focus Shield: blocking that follows the plan** (S/M)
- **How it works.** Use Chrome MV3 `declarativeNetRequest` dynamic rules in `extension/background.js`. Today the extension's permissions are only `storage`, `alarms` and `notifications`.
- **Turns itself on:**
  - when an Active session is running (poll `/api/active/current`), or
  - when a scheduled block starts.
- **Turns itself off** when the block ends, so there are no manual schedules like Opal's.
- **Blocklist:** seeded with common distractors, plus sites the student picks. Optionally learned from `/active` distraction detections.
- **Unlock:** finish the block, or pass a 30–60 second friction pause, using the existing `stakes` and `takeover` enforcement modes as the policy.
- **Why this beats Opal:**
  - Free versus about $100/year.
  - Tied to the plan.
  - Works on **Chromebooks, where K-12 homework actually happens**.
- **Privacy:** browsing is never reported to parents or teachers. Say this loudly.
- **Caveats:**
  - Managed school Chromebooks may block extension installs. That feeds the district channel in `distribution.md`.
  - Phone app blocking needs native iOS FamilyControls and Android accessibility APIs. That is L effort; defer it.

**4. Live plan feed (webcal)** (S)
- **Endpoint:** `GET /calendar/<revocable-token>.ics`, subscribable as `webcal://`.
- **Contents:** planned study blocks, due dates and tests.
- **Updates:** on every reflow or autopilot change.
- **Why it's better than the Canvas feed:** it carries the *plan*, not just deadlines, and it re-flows.
- **Current state:** today `ics_feed.py` only *imports* feeds and `/schedule/export.ics` is a one-shot POST.
- **Why it matters:** it puts IntelliPlan in the default Calendar app on the phones 88% of US teens use, without waiting for App Store approval.

**5. Study Buddies (co-op, not competitive)** (M)
- **Invites:** invite up to 5 friends via a link. Reuse the referral plumbing: `/ref/<code>`, `/api/referral`.
- **Shared streak:** each pair gets a "we both studied today" streak, modeled on Duolingo's per-friend streaks. One broken pair doesn't break the others.
- **Nudges** tied to plan events: "Maya finished her block. Your 25 minutes are up next."
- **Pet visits:** a friend visit to the pet, via the existing `/api/pet/visit`.
- **What is shared:** only streak days and minutes. **Never grades.**
- **Age gating:** under-13 users go through the existing `/account/age` and parent-consent flow.
- **Why co-op beats leaderboards:** leaderboards like Gizmo's make struggling students feel worse. Every invite is also acquisition.
- **Reuse** `/api/groups`, which already has voice presence, for "study together now."

**6. Quick-add everywhere** (S)
- **Surfaces:**
  - A Ctrl/Cmd-K palette on web.
  - An extension omnibox keyword, for example `ip bio lab due fri 2h`.
  - The Android share target and quick tile, and the desktop tray (`desktop/`).
- **Parser:** a deterministic fast-path for dates, durations and course aliases, so capture is instant and works offline. Fall back to the existing `/api/tasks/extract` LLM.
- **Better than Todoist:** after capture it *places* the task in the plan and says where ("Added → Thu 4:30, 2h, Bio").

**7. Timetable that fills itself** (M)
- **Model:** a `ClassMeeting` model with course, start and end times, room, and a rotation pattern. Rotation types:
  - weekday lists
  - A/B day
  - N-day cycles
  - Week 1/2
  - plus bell-schedule presets and no-school days
- **Auto-import, instead of MSL's manual entry or paid photo scan:**
  1. StudentVUE class schedule (periods, times, rooms). Needs confirming against what `studentvue_helper.py` receives; it already reads `period`.
  2. Schoology sections.
  3. A photo of the printed schedule, through the existing vision route (`/study/analyze-image`).
- **Feeds the scheduler.** Timetable data flows into availability windows (`windows_for`), so plans never collide with class.
- **Enables** the "next class" widget (gap 9) and the timeline (gap 8).
- **Free.** MSL charges for rotations.

**8. Today timeline** (S/M)
- **Layout:** a vertical, time-proportional day view merging:
  - classes (gap 7)
  - planned blocks
  - Google and Outlook busy time (`/calendar/events`)
- **Live "now" line** plus a remaining-time ring on the current block.
- **Drag a block** to call `/schedule/reflow`, with the consequence preview already built.
- **Better than Structured and Tiimo:** it is filled automatically from real school data. Nobody has to type their day.

**9. Widgets and Live Activities** (L)
- **Widgets:** "Next up" and "Due today" widgets, plus a Lock Screen Live Activity for the running focus session.
- **Implementation:** Expo config plugins or extra native targets for iOS WidgetKit and Android App Widgets, fed by `/api/snapshot`.
- **Dependency:** this only matters once the store apps ship (`distribution.md`, move 1). In the meantime, the webcal feed (gap 4) provides most of the "glanceable" value.

**10. Classmate decks** (M)
- **Trigger:** when two or more users share an LMS course ID, offer: "3 classmates made decks for *Unit 4 Test* (Fri)."
- **Sharing:** opt-in, scoped to the class. Import through the existing deck API.
- **Better than Quizlet:** the SRS automatically schedules reviews *backwards from that student's test date* (`/api/tests`), which IntelliPlan knows and Quizlet has to ask for.
- **Scope:** deliberately no public marketplace. That avoids moderation and copyright load.

### Gaps not worth closing (honest calls)

- **Live lecture capture (Turbo, StudyFetch, Coconote).** The demand is real, but the space is crowded and well-funded. Quizlet bought Coconote. Transcription costs recur. Recording in K-12 classrooms raises consent and district-policy problems. IntelliPlan already covers upload-based lessons (`/lessons`, `/study/transcribe`). Better to position it as "bring your Turbo or Otter notes in," not to compete.
- **Public deck marketplace.** Quizlet and Knowt have compounding content moats and trust-and-safety teams. Class-scoped sharing (gap 10) gets most of the value.
- **Habit tracker, meeting links, "defended" recurring blocks.** These are adult-productivity features. They dilute the "school on autopilot" story and won't move student retention.
- **Motion-style silent minute-by-minute rescheduling.** Users complain about exactly this. IntelliPlan's "show the consequence, then apply" design is the better one. Keep it.
- **Real-tree planting.** Nice for brand, but it costs money per tree and is a Forest signature. Revisit only as a partnership.
- **Native phone app blocking.** Only after the store apps ship, and only if the Chrome/desktop Focus Shield (gap 3) shows usage.

### Data caveats
- **User counts:** these are the vendors' own claims or third-party estimates (Turbo, Opal, Structured and Knowt especially). None are audited.
- **Pricing:** varies by region, platform and source (TickTick, Knowt, Reclaim and Forest had conflicting figures). Ranges are given where sources disagree.
- **Reddit sentiment:** second-hand only, because reddit.com was blocked by egress policy in this session.
- **StudentVUE timetable fields:** not verified against the live API.
