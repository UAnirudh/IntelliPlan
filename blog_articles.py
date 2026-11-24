"""The blog's article registry.

One list, rendered by one template. Before this, every article was its own
~180-line HTML file that repeated the same inline stylesheet, the same CTA
markup and its own hand-written sitemap tuple — so adding ten guides meant
ten more copies of the CSS and three places to forget to update.

The shape here is deliberately boring data: a slug, the metadata a search
result and a social card need, and an ordered list of sections. Paragraphs
are plain text, never HTML, so nothing an article contains can smuggle markup
into the page; internal links are declared explicitly and rendered by the
template. ``blog_article.html`` does the rendering and emits the Article
JSON-LD; App.py registers ``/blog/<slug>`` from the same list that feeds
``/blog`` and ``sitemap.xml``, so a published article cannot be orphaned from
either.

Adding an article: append a dict. Nothing else needs editing.
"""

from __future__ import annotations

ARTICLES: list[dict] = [
    {
        "slug": "intelliplan-vs-nerdania",
        "path": "/compare/intelliplan-vs-nerdania",
        "kicker": "Comparison",
        "title": "IntelliPlan vs Nerdania for exam prep",
        "meta_title": "IntelliPlan vs Nerdania | Free Study Planner Comparison",
        "description": "Compare planning from class notes for one exam with organizing assignments and study blocks across a school week.",
        "date": "2026-11-24",
        "reading_time": 3,
        "sections": [
            {"paragraphs": ["Nerdania starts with class material and an exam date. IntelliPlan starts with the assignments, deadlines, and commitments already competing for your time. Both approaches can help during exam season, depending on what you need to work out first."]},
            {"h2": "Choosing what to review", "paragraphs": ["Nerdania describes generating a day-by-day review plan, flashcards, and a quiz from notes or slides you provide. That can help when a stack of material is the main obstacle. In IntelliPlan, Plani Tutor can guide practice, and the planner can place review sessions alongside the rest of your workload."], "links": [
                {"url": "https://www.nerdania.com/", "label": "Nerdania’s study plan workflow"},
                {"url": "/tutor", "label": "Explore guided practice with Plani"},
            ]},
            {"h2": "Try an exam week with two other deadlines", "paragraphs": ["Set a biology exam for Friday, a lab due Wednesday, and an essay due Thursday. Give yourself only an hour on Tuesday. In a notes-first workflow, check whether the biology plan gives you the right topics to review. In IntelliPlan, check whether your available time still covers the lab and essay. If it doesn’t, the remaining-work list shows what needs time first."], "links": [{"url": "/demo/planner", "label": "Try a sample school week without an account"}]},
        ],
        "related": ["study-planner", "intelliplan-vs-studysmarter"],
    },
    {
        "slug": "intelliplan-vs-plannly",
        "path": "/compare/intelliplan-vs-plannly",
        "kicker": "Comparison",
        "title": "IntelliPlan vs Plannly",
        "meta_title": "IntelliPlan vs Plannly | Free Planners for Students",
        "description": "Compare Plannly’s offline planner and syllabus-based revision with IntelliPlan’s school connections and calendar-aware scheduling.",
        "date": "2026-11-24",
        "reading_time": 3,
        "sections": [
            {"paragraphs": ["Plannly advertises offline planning and the ability to start without an account. IntelliPlan is designed around coursework from supported school connections and busy time from supported calendars. Both can help answer what to study today."]},
            {"h2": "An exam routine or a connected school week", "paragraphs": ["Plannly says it spreads syllabus topics across the days before an exam, respects days off, and rolls missed work into the next plan. IntelliPlan can bring deadlines in from Canvas, StudentVUE, or Schoology where access is available. Its schedule surfaces work that couldn’t be placed before a deadline."], "links": [
                {"url": "https://plannly.org/", "label": "Plannly’s features and planning workflow"},
                {"url": "/faq", "label": "Check IntelliPlan’s connection options"},
            ]},
            {"h2": "Test the difference with one disrupted week", "paragraphs": ["Start with two days off and a math exam next Friday. Miss one study session and move a homework deadline earlier. In Plannly, see where the missed exam topic goes. In IntelliPlan, check whether the new deadline and remaining homework are visible when you review the plan. Use fictional assignments for the comparison so you can try it before sharing school information."], "links": [{"url": "/demo/planner", "label": "Try replanning a fictional week"}]},
        ],
        "related": ["study-planner", "intelliplan-vs-nerdania"],
    },
    {
        "slug": "intelliplan-vs-reclaim",
        "path": "/compare/intelliplan-vs-reclaim",
        "kicker": "Comparison",
        "title": "IntelliPlan vs Reclaim for students",
        "meta_title": "IntelliPlan vs Reclaim | Study and Calendar Planning",
        "description": "Compare Reclaim’s flexible Focus Time and task integrations with IntelliPlan’s homework imports, deadline coverage, and study sessions.",
        "date": "2026-11-24",
        "reading_time": 3,
        "sections": [
            {"paragraphs": ["Reclaim helps protect flexible time for focus, habits, and work. IntelliPlan builds a coursework plan around your available time and shows which assignments still need a slot. The best starting point is the calendar or school portal you already use."]},
            {"h2": "How each app protects study time", "paragraphs": ["Reclaim’s current documentation explains how its Focus Time agent protects work windows and recommends tasks inside them. Reclaim Chat can preview a calendar block for your approval. IntelliPlan can place coursework into specific sessions, then show the assignments it couldn’t fit before their deadlines."], "links": [
                {"url": "https://help.reclaim.ai/en/articles/16558552-tasks-overview-decide-what-to-work-on-when", "label": "Reclaim’s guide to tasks and Focus Time"},
                {"url": "/study-planner", "label": "How IntelliPlan builds a study plan"},
            ]},
            {"h2": "Try it with a moved commitment", "paragraphs": ["Add a 50-minute reading due tomorrow, then move practice into your normal study hour. In Reclaim, see whether the protected Focus Time moves and the reading is easy to find. In IntelliPlan, regenerate the study plan and check its remaining-work list. This gives you a quick way to judge whether you can adjust before the deadline."], "links": [{"url": "/demo/planner", "label": "Try the synthetic study week"}]},
        ],
        "related": ["study-planner", "intelliplan-vs-motion"],
    },
    {
        "slug": "intelliplan-vs-todoist",
        "path": "/compare/intelliplan-vs-todoist",
        "kicker": "Comparison",
        "title": "IntelliPlan vs Todoist for coursework",
        "meta_title": "IntelliPlan vs Todoist | Homework and Time Blocking",
        "description": "Compare Todoist projects and calendar time blocking with IntelliPlan’s school imports, homework scheduling, and study tools.",
        "date": "2026-11-24",
        "reading_time": 3,
        "sections": [
            {"paragraphs": ["Todoist is designed to capture and organize tasks across projects. IntelliPlan connects a student’s assignments, available study hours, and study practice. You can keep a Todoist project for everyday tasks and import selected projects into IntelliPlan where the connection is available."]},
            {"h2": "Time blocking an assignment", "paragraphs": ["Todoist’s calendar layout lets you drag a task onto the day and adjust its duration. Todoist lists that layout under Pro and Business. In IntelliPlan, give the planner a deadline, duration, and study availability, then review its proposed study blocks and remaining work."], "links": [
                {"url": "https://www.todoist.com/help/todoist/get-started/plan-your-day-with-the-today-view-UVUXaiSs", "label": "Todoist’s guide to the Today calendar"},
                {"url": "/study-planner", "label": "How IntelliPlan schedules assignments"},
            ]},
            {"h2": "A two-day test", "paragraphs": ["Add a 90-minute essay and two 45-minute study windows. In Todoist, try blocking the work in your calendar. In IntelliPlan, try scheduling the sessions around the commitments you already have. Now remove one window and see how quickly you can identify the time left to find."], "links": [{"url": "/demo/planner", "label": "Try IntelliPlan without connecting accounts"}]},
        ],
        "related": ["study-planner", "intelliplan-vs-reclaim"],
    },
    {
        "slug": "intelliplan-vs-studysmarter",
        "path": "/compare/intelliplan-vs-studysmarter",
        "kicker": "Comparison",
        "title": "IntelliPlan vs StudySmarter",
        "meta_title": "IntelliPlan vs StudySmarter | Study Planning and Flashcards",
        "description": "Compare StudySmarter’s flashcards and revision tools with IntelliPlan’s deadline-aware study planner, calendar, and guided Tutor.",
        "date": "2026-11-24",
        "reading_time": 3,
        "sections": [
            {"paragraphs": ["StudySmarter emphasizes turning study material into flashcards, summaries, and revision. IntelliPlan brings coursework and school deadlines into a calendar-aware planner, with options for tutoring, flashcards, and active study."]},
            {"h2": "Organizing revision and schoolwork", "paragraphs": ["StudySmarter publishes multiple flashcard study modes and tools for working with notes. IntelliPlan is a good fit when revision competes with homework, classes, and activities. You can build study blocks around available time and use Plani to work through a concept or quiz yourself."], "links": [
                {"url": "https://www.studysmarter.co.uk/", "label": "StudySmarter’s study tools"},
                {"url": "/tutor", "label": "Explore Plani Tutor"},
            ]},
            {"h2": "Check your actual workflow", "paragraphs": ["Use the same fictional vocabulary list and a test next Friday. Compare how easily you can review the concepts you missed and find a realistic time to return to them. If your assignments come from a school portal, check the connection before choosing a planner."], "links": [{"url": "/faq", "label": "Check IntelliPlan’s school connections"}]},
        ],
        "related": ["study-planner", "intelliplan-vs-shovel"],
    },
    {
        "slug": "intelliplan-vs-shovel",
        "path": "/compare/intelliplan-vs-shovel",
        "kicker": "Comparison",
        "title": "IntelliPlan vs Shovel for students",
        "meta_title": "IntelliPlan vs Shovel | Study Planner Comparison",
        "description": "Compare Shovel's Cushion and school imports with IntelliPlan's deadline coverage, calendar planning, and tutoring.",
        "date": "2026-11-23",
        "reading_time": 3,
        "sections": [
            {"paragraphs": [
                "Shovel and IntelliPlan both turn assignments into study blocks and flag work that may miss a deadline. Shovel is a strong option if your school uses Brightspace or Moodle. IntelliPlan is worth trying if you use StudentVUE or Schoology, or want a planner that also gives you study practice and grade modeling.",
            ]},
            {"h2": "Compare school connections first", "paragraphs": [
                "Shovel lists Canvas, Brightspace, Moodle, Google Classroom, and PDF syllabus import. IntelliPlan supports Canvas, StudentVUE, and Schoology where available. Check which connection your school actually allows before choosing a planner.",
            ], "links": [
                {"url": "https://shovelapp.io/", "label": "Shovel’s official planner and integration overview"},
                {"url": "/faq", "label": "Check IntelliPlan’s school connections"},
            ]},
            {"h2": "Run the same deadline test", "paragraphs": [
                "Give each planner one free hour and add a 45-minute lab plus a 30-minute reading due before your next open slot. Shovel’s Cushion shows how far short you are. IntelliPlan’s Scheduler and Command Center list work that still needs time and its deadlines. With these estimates at least 15 minutes need another slot. Check whether you can identify the affected assignment and change the plan without re-entering your work.",
            ], "links": [{"url": "/demo/planner", "label": "Try IntelliPlan with a fictional week"}]},
            {"h2": "What happens after the plan?", "paragraphs": [
                "Shovel publishes AI connectors for students who want to manage plans through supported external assistants. In IntelliPlan, Command Center can draft a week in the app, and Tutor can help you reason through a problem. Try the Grade Modeler if deciding which assignment to prioritize depends on how much it counts.",
            ], "links": [
                {"url": "/tutor", "label": "See Plani Tutor"},
                {"url": "/grademodel", "label": "See Grade Modeler"},
            ]},
        ],
        "related": ["study-planner", "intelliplan-vs-matcha"],
    },
    {
        "slug": "intelliplan-vs-motion",
        "path": "/compare/intelliplan-vs-motion",
        "kicker": "Comparison",
        "title": "IntelliPlan vs Motion for students",
        "meta_title": "IntelliPlan vs Motion | Calendar Planning for Students",
        "description": "Compare Motion's work calendar, priorities, and deadlines with IntelliPlan's course imports, study blocks, and tutoring.",
        "date": "2026-11-23",
        "reading_time": 3,
        "sections": [
            {"paragraphs": [
                "Motion automatically schedules tasks and tracks dependencies for work and team projects. IntelliPlan schedules coursework around available study time and links it with tutoring and practice. Start with where your deadlines live and what you do once a work block starts.",
            ]},
            {"h2": "A deadline on a team project or in a course", "paragraphs": [
                "Motion describes custom scheduling windows, deadlines, task priorities, and project dependencies. IntelliPlan can bring in assignments from supported school portals and supply busy time from connected calendars. If you rely on a school import, confirm that your portal and school permissions work before migrating.",
            ], "links": [
                {"url": "https://www.usemotion.com/features/ai-task-manager", "label": "Motion’s official task manager overview"},
                {"url": "/study-planner", "label": "See how IntelliPlan builds a plan"},
            ]},
            {"h2": "Try a week with a late change", "paragraphs": [
                "Pick a week with a class, work shift, and essay. Move the shift two hours later after you make the first plan. Motion advertises automatic rescheduling when the day changes, plus deadline warnings. In IntelliPlan, check the imported commitment and review which remaining blocks and deadlines need your attention after replanning.",
                "If the essay is still missing time, compare how quickly you can see the shortfall, decide whether to move another commitment, and start your next block.",
            ], "links": [{"url": "/demo/planner", "label": "Try IntelliPlan with a fictional week"}]},
            {"h2": "From the calendar to the task", "paragraphs": [
                "Motion fits team workflows with assigned work and dependencies. IntelliPlan puts class planning alongside Active Study, practice questions, and an AI tutor. For a student, test whether you can go from an upcoming deadline to a useful study session without recreating context in another app.",
            ], "links": [{"url": "/tutor", "label": "See Plani Tutor"}]},
        ],
        "related": ["study-planner", "intelliplan-vs-shovel"],
    },
    {
        "slug": "study-planner",
        "path": "/study-planner",
        "kicker": "Planner",
        "title": "A study planner that shows what fits",
        "meta_title": "Calendar-Aware Study Planner | IntelliPlan",
        "description": "Schedule assignments around your real availability, see which deadlines need more study time, and try a sample week before connecting an account.",
        "date": "2026-11-23",
        "reading_time": 3,
        "sections": [
            {"paragraphs": [
                "IntelliPlan turns assignments into study blocks around your available hours. Command Center and Scheduler both show work the plan couldn’t place, including how many minutes remain and the earliest affected deadline. You can try the workflow with fictional coursework before connecting accounts.",
            ], "links": [{"url": "/demo/planner", "label": "Try the sample week"}]},
            {"h2": "What goes into the plan?", "list_style": "ol", "list": [
                "Bring in assignments from a supported school connection, or add them yourself. Check that due dates and time estimates match your coursework.",
                "Set your study hours and add commitments such as work, travel, and practice. A connected Google or Outlook calendar can supply busy time where you grant access. Your class timetable can also reserve time.",
                "Review the proposed blocks and remaining work together. If you have a tight deadline, check how much time is still missing before you commit to the week.",
            ], "links": [
                {"url": "/blog/how-to-use-canvas-with-a-study-planner", "label": "Plan around Canvas assignments"},
                {"url": "/blog/studentvue-study-planner", "label": "Plan around StudentVUE assignments"},
                {"url": "/schoology", "label": "Check Schoology connection requirements"},
            ]},
            {"h2": "Worked example: two assignments and one free hour", "paragraphs": [
                "Suppose Monday has 60 minutes of usable study time after practice, travel, and dinner. You estimate 25 minutes to review for a quiz due Tuesday at 8:30 a.m. and 50 minutes to finish a reading due Tuesday evening. Both are due before your next available study block.",
                "That is 75 minutes of work for 60 available minutes. Even before breaks or work that cannot be split, at least 15 minutes need another slot. If the plan places 25 minutes of quiz review and 35 minutes of reading, it shows the reading with 15 minutes left to schedule. If the quiz didn’t fit, its morning deadline would appear ahead of the evening reading.",
                "Use the availability shortcut when you can genuinely free up more time. If you can’t, check whether your estimate is realistic or ask the teacher about the deadline. When none of the work can fit, Command Center returns a preview and preserves your previous saved plan.",
            ]},
            {"h2": "When a commitment moves", "paragraphs": [
                "Refresh the connected source if the change is recent. Then review the remaining work in Scheduler and adjust the plan. On eligible accounts, Autopilot can rebuild a saved plan after supported sources refresh when enabled. Review any work the updated plan still can’t place.",
            ], "links": [
                {"url": "/blog/fit-schoolwork-around-your-calendar", "label": "Set up calendar-aware planning"},
                {"url": "/compare/intelliplan-vs-matcha", "label": "Compare IntelliPlan and Matcha"},
            ]},
        ],
        "related": ["fit-schoolwork-around-your-calendar", "reset-your-study-plan-after-a-missed-day"],
    },
    {
        "slug": "turn-a-task-list-into-a-study-plan",
        "kicker": "Planning",
        "title": "Turn a Messy Task List Into a Study Plan",
        "meta_title": "Turn a Pasted Task List Into a Study Plan | IntelliPlan",
        "description": "Use IntelliPlan’s task preview to turn loose deadlines and homework notes into a list you can schedule.",
        "date": "2026-11-19",
        "reading_time": 2,
        "sections": [
            {"paragraphs": [
                "On Sunday night, you might have a chemistry reminder in a notebook, an essay deadline in a message, and a reading assignment on the back of a worksheet. You can bring those together in a single pass. Start with the next seven days and write each task as a line you could act on.",
            ]},
            {"h2": "Write one task per line", "paragraphs": [
                "Try “Read chapter 6 due Friday 30m” or “Outline history essay due Thursday 45m.” Start each line with an action. Add a clear deadline and an estimate for the work in that line. For an essay that needs two evenings, a line for the outline and a line for the draft make each block easier to start.",
                "Use a date when a weekday could mean more than one thing. Leave a date out if you need to check it first. An undated task you remember to verify is easier to recover from than a guessed deadline.",
            ]},
            {"h2": "Review the tasks before adding them", "paragraphs": [
                "Open Add work in IntelliPlan’s scheduler. Paste the list and select Review tasks. Check the preview for the deadlines and durations you intended. If a line came through wrong, change the text and preview it again. Select Add these tasks when the list matches your notes.",
                "Before making the plan, check for duplicates from any school portal you already connected. Give yourself room for one assignment that runs over its estimate. If you have 90 minutes tonight, plan 70 before filling the remaining 20.",
            ], "links": [
                {"url": "/scheduler?capture=1", "label": "Open task capture in IntelliPlan"},
                {"url": "/demo/planner", "label": "Try a sample plan"},
            ]},
        ],
        "related": ["fit-schoolwork-around-your-calendar", "reset-your-study-plan-after-a-missed-day"],
    },
    {
        "slug": "bring-todoist-and-notion-into-your-study-plan",
        "kicker": "Integrations",
        "title": "Bring Todoist and Notion Tasks Into Your Study Plan",
        "meta_title": "Study Planning With Todoist and Notion | IntelliPlan",
        "description": "Choose school projects in Todoist or a task database in Notion, then check what belongs in this week’s homework plan.",
        "date": "2026-11-19",
        "reading_time": 2,
        "sections": [
            {"paragraphs": [
                "Your notes and task apps can stay where you like them. Give your study planner the pieces it needs to schedule this week. For a science project, that might be three dated milestones from Todoist and one lab write-up from a Notion database you already use.",
            ]},
            {"h2": "Choose a small set of Todoist projects", "paragraphs": [
                "In the scheduler, open Add work, then Sync Todoist. When the connection is available for your account, sign in and select the projects you want to use. Start with one school project and choose Save and sync. You can select up to four. The sync reads tasks from the projects you select.",
                "If you have a general inbox, move school items into a project first or add just the work you need with a project CSV. Choose the file under Add work and review it before saving.",
            ]},
            {"h2": "Give Notion a dedicated database", "paragraphs": [
                "In Settings, open Integrations and connect Notion. Share your school task database with the integration, then use Choose database. Keep actionable items with clear titles and dates together. A database row titled “Submit lab reflection” with Friday as the due date gives the planner more to work with than a row named “Chemistry.”",
                "Use the same source for a task as its date changes. If a deadline appears in both Canvas and your own project, check the upcoming work before planning so you only schedule it once.",
            ], "links": [
                {"url": "/scheduler?capture=1", "label": "Open Add work"},
                {"url": "/settings#integrationsWrap", "label": "Open integration settings"},
            ]},
        ],
        "related": ["turn-a-task-list-into-a-study-plan", "how-to-use-canvas-with-a-study-planner"],
    },
    {
        "slug": "ask-plani-for-a-hint",
        "kicker": "Tutoring",
        "title": "How to Ask Plani for a Hint and Keep Solving",
        "meta_title": "How to Ask Plani for a Homework Hint | IntelliPlan",
        "description": "Try three short prompts that help you find the next step, check your reasoning, and practice with a fresh question.",
        "date": "2026-11-19",
        "reading_time": 2,
        "sections": [
            {"paragraphs": [
                "The most useful thing to bring into a tutoring chat is what you’ve already tried. Even a crossed-out first step tells Plani where to start. Pick one question and write down the point where your reasoning stopped.",
            ]},
            {"h2": "Ask for the smallest next step", "paragraphs": [
                "In Tutor, try “I have 3(x − 2) = 15. I think I should divide by 3 first. Give me one hint and let me do the work.” Follow the hint on paper and come back with the line you wrote. If your class expects a particular method, name it in your question.",
                "On a worksheet with sensitive information in the header, type the part you’re working on. A short prompt gives the tutor a clear job and lets you leave names and grades out of the conversation.",
            ]},
            {"h2": "Check what you think you know", "paragraphs": [
                "When you have a result, explain why it works. Try “I divided both sides by 3 because it keeps the equation balanced. Check that explanation and tell me the first step you’d fix.” Compare the feedback with a worked example or your class notes.",
            ]},
            {"h2": "Finish with a different example", "paragraphs": [
                "Ask for one similar practice question and try it before requesting feedback. If the same mistake comes back, write it down as a prompt for tomorrow. For example, “When do I need to distribute before combining terms?” A question you can retry later is more useful than a long chat you have to reread.",
            ], "links": [{"url": "/tutor", "label": "Open Tutor and ask Plani"}]},
        ],
        "related": ["active-recall-vs-rereading", "how-to-study-for-math-tests"],
    },
    {
        "slug": "reset-your-study-plan-after-a-missed-day",
        "kicker": "Planning",
        "title": "Reset Your Study Plan After a Missed Day",
        "meta_title": "Reset a Study Plan After a Missed Day | IntelliPlan",
        "description": "Use a quick check-in and a realistic timeline to move unfinished work without overloading tomorrow.",
        "date": "2026-11-19",
        "reading_time": 2,
        "sections": [
            {"paragraphs": [
                "A missed evening changes how much time you have. Start by separating what you finished from what still needs time. If you wrote half of a lab report, plan the analysis and revision that remain. The original estimate already includes work you did.",
            ]},
            {"h2": "Compare the deadlines with your real time", "paragraphs": [
                "Say a lab is due Wednesday and a history reading is due Friday. Tuesday has a 45-minute opening after dinner. Use it for the lab, then put the reading into a later opening. If the lab still needs more than 45 minutes, decide which part needs to be ready first and look for one smaller session before the deadline.",
                "Protect the things that make the next day workable, including meals, travel, and sleep. If a deadline no longer fits, knowing early gives you a chance to ask your teacher which part to prioritize.",
            ]},
            {"h2": "Record where to resume", "paragraphs": [
                "In the scheduler’s My plan view, open Daily check-in. Choose Continue tomorrow and write one short next step, such as “Add measurements to the lab graph.” The note stays in this browser.",
                "Open Adjust the plan and review the proposed changes before applying them. Check that the nearest deadline still has enough time and that the first new block has a clear starting point. You can also practice adjusting a sample week before using your own courses.",
            ], "links": [
                {"url": "/scheduler", "label": "Open your study plan"},
                {"url": "/demo/planner", "label": "Practice on a sample week"},
            ]},
        ],
        "related": ["fit-schoolwork-around-your-calendar", "how-to-catch-up-when-behind-in-school"],
    },
    {
        "slug": "turn-a-photo-into-calendar-events",
        "kicker": "Planning",
        "title": "Turn a Syllabus Photo Into Calendar Events",
        "meta_title": "Turn a Syllabus Photo Into Calendar Events | IntelliPlan",
        "description": "Review the dates in a handout or class timetable before saving tasks, events, or a class in IntelliPlan.",
        "date": "2026-11-19",
        "reading_time": 2,
        "sections": [
            {"paragraphs": [
                "A printed syllabus mixes things you need to do with places and times you need to be. Sort those into tasks, calendar events, and recurring classes. “Read chapter 4 by Tuesday” needs study time. “Meet at the library Tuesday at 3:30” already has a time. “Biology lab each Thursday” belongs on the class timetable.",
            ]},
            {"h2": "Photograph the dates you need", "paragraphs": [
                "Start with the part of the page that holds the work, such as the first month of deadlines. Take the photo straight on with good light. Crop out classmates’ names, grades, and parts of the page you do not need for planning. If dates are too blurry to read, take a clearer photo or type those lines in Add work.",
            ]},
            {"h2": "Check the preview against the handout", "paragraphs": [
                "On supported accounts, open Read a photo. Check the extracted text, then select Find events and classes. Review the proposed entries before saving. Pay special attention to months and times on tables, the end time of an event, and which days repeat for a class. If a date or time is uncertain, confirm it with the course syllabus before you save it.",
                "Save recurring classes to your timetable. For one-off events, add them to a connected Google Calendar or download the calendar file and import it into the calendar you use. Check that the events and classes appear at the right times before planning homework around them.",
            ], "links": [
                {"url": "/flashcards/photo", "label": "Read a photo in IntelliPlan"},
                {"url": "/scheduler?capture=1", "label": "Paste a task list"},
            ]},
        ],
        "related": ["turn-a-task-list-into-a-study-plan", "fit-schoolwork-around-your-calendar"],
    },
    {
        "slug": "intelliplan-vs-matcha",
        "path": "/compare/intelliplan-vs-matcha",
        "kicker": "Comparison",
        "title": "IntelliPlan vs Matcha",
        "meta_title": "IntelliPlan vs Matcha | Study Planner Comparison",
        "description": "Compare how IntelliPlan and Matcha schedule schoolwork, refresh connected calendars, and help you prepare for assignments and exams.",
        "date": "2026-11-18",
        "reading_time": 3,
        "sections": [
            {"paragraphs": [
                "Both apps help students make time for assignments imported from Canvas or StudentVUE. IntelliPlan is a good fit if your school uses Schoology, you want to try a sample planner before connecting an account, or you want to work backward from a target grade.",
                "IntelliPlan published this comparison. We reviewed Matcha’s public FAQ and its November 6 comparison on November 18, 2026.",
            ]},
            {"h2": "Build around the week you already have", "paragraphs": [
                "IntelliPlan can use busy time from calendars on the connected Google account, with permission to read availability. It also supports Outlook and a class timetable. Study estimates can adapt to recorded session history. Autopilot can revise a saved plan after supported sources refresh when it is enabled.",
                "Matcha uses Google Calendar availability and completion history to plan sessions. It documents configurable refresh schedules for connected sources. In either app, a changed deadline can take time to appear after a teacher updates it.",
            ], "links": [
                {"url": "/blog/fit-schoolwork-around-your-calendar", "label": "Set up calendar-aware planning in IntelliPlan"},
                {"url": "https://www.trymatcha.org/faq", "label": "Matcha FAQ"},
            ]},
            {"h2": "Bring in work and start a session", "paragraphs": [
                "In IntelliPlan, eligible personal accounts can import tasks from up to four selected Todoist projects, and a Notion connection can import tasks from a selected database. On supported accounts you can review and confirm tasks, calendar events, or class entries from a photo. A planned block can open a focus timer and checklist.",
                "Matcha also documents Todoist and Notion imports, turning a photo into tasks and events, and a Focus Lock timer that stays visible as you navigate.",
            ], "links": [
                {"url": "/demo/planner", "label": "Try building and adjusting a sample IntelliPlan week"},
                {"url": "https://www.trymatcha.org/compare/intelliplan", "label": "Read Matcha’s November 6 comparison"},
            ]},
            {"h2": "Choose the support you need when you sit down to study", "paragraphs": [
                "Matcha’s Mochi offers multiple-choice practice with explanations and accepts images and PDFs in chat. IntelliPlan Tutor can show graphs and tables, offer flashcards and quizzes, and build a study roadmap with resources. On supported accounts you can start with an assignment from your planner or turn a missed practice question into a review task.",
                "Matcha shows what an upcoming assignment score would do to your class grade. IntelliPlan’s Grade Modeler lets you simulate scores across remaining assignments, work backward from a target, and draft review time for the pending assignment with the largest modeled grade impact. The separate final-grade calculator can answer a quick what-if without an account.",
            ], "links": [
                {"url": "/tutor", "label": "Explore IntelliPlan Tutor"},
                {"url": "/grademodel", "label": "Explore Grade Modeler"},
                {"url": "/tools/final-grade-calculator", "label": "Try the public final-grade calculator"},
            ]},
            {"h2": "Check your school, device, and plan", "paragraphs": [
                "IntelliPlan supports Schoology at schools that allow its API connection. It can be installed as a web app on supported browsers and offers a school information page and developer documentation. Use the pricing page for current free and paid plan details.",
                "Matcha’s FAQ lists Canvas and StudentVUE as its school connections. It says Matcha is currently free and plans to introduce subscriptions.",
            ], "links": [
                {"url": "/schoology", "label": "See Schoology setup"},
                {"url": "/install", "label": "Install IntelliPlan"},
                {"url": "/schools", "label": "IntelliPlan for schools"},
                {"url": "/api-docs", "label": "Developer documentation"},
                {"url": "/pricing", "label": "See current IntelliPlan pricing"},
                {"url": "https://www.trymatcha.org/faq", "label": "See Matcha’s current FAQ"},
            ]},
        ],
        "related": ["fit-schoolwork-around-your-calendar", "how-to-use-canvas-with-a-study-planner", "spaced-repetition-for-students"],
    },
    {
        "slug": "fit-schoolwork-around-your-calendar",
        "kicker": "Planning",
        "title": "How to Plan Homework Around Practice, Shifts, and Classes",
        "meta_title": "Plan Homework Around Your Calendar | IntelliPlan",
        "description": "Use calendar availability, movable study blocks, and realistic time estimates to fit homework around practice, shifts, and classes.",
        "date": "2026-11-18",
        "reading_time": 3,
        "sections": [
            {"paragraphs": [
                "Start with commitments that already have a time. If practice ends at 5, block travel and dinner too. Then put the assignment with the nearest deadline or highest consequence into the first gap long enough to make progress.",
                "For example, if you’re free from 6:15 to 7:30, give tomorrow’s math set 40 minutes, take a 10-minute break, and use the final 25 minutes to outline Friday’s essay. If math consistently takes longer than expected, split the set over two days or give it a longer block next week.",
            ]},
            {"h2": "Give the planner your real availability", "paragraphs": [
                "In IntelliPlan, set your study hours in the scheduler. If you use Google Calendar, connect the account that holds your commitments and grant availability access so busy time can affect the plan. Outlook availability and your IntelliPlan class timetable can also affect the time available.",
                "If an important commitment lives on a calendar you haven’t connected, block off the time yourself. Check the first plan for travel, meals, and breaks before you start following it.",
            ]},
            {"h2": "Adjust the work that remains", "paragraphs": [
                "If practice runs late, move or rebuild the unfinished blocks from the scheduler. Review any work that no longer fits before its deadline. When a supported school connection refreshes and Autopilot is enabled, check the updated plan for changes.",
                "In the sample planner, build a plan with 30 minutes available each afternoon. Mark the first block missed, then adjust remaining work. Try again with 60 minutes and see which tasks can now share a day.",
            ], "links": [
                {"url": "/demo/planner", "label": "Try the sample planner without a school account"},
            ]},
        ],
        "related": ["intelliplan-vs-matcha", "how-to-use-canvas-with-a-study-planner", "studentvue-study-planner"],
    },
    {
        "slug": "how-to-study-for-finals-in-one-week",
        "kicker": "Finals",
        "title": "How to Study for Finals in One Week (Without Cramming)",
        "meta_title": "How to Study for Finals in One Week | IntelliPlan",
        "description": "A realistic seven-day finals plan: what to study first, how long to spend, and how to walk in rested instead of wrecked.",
        "date": "2026-11-14",
        "reading_time": 6,
        "sections": [
            {"paragraphs": [
                "One week is not enough time to learn a semester from scratch, and pretending otherwise is how students end up awake at 3am with six half-read chapters and no confidence. It is, however, plenty of time to study well if you spend it on the right things in the right order.",
                "The mistake almost everyone makes is studying by subject instead of by value. Seven days divided by six classes gives each subject a little over a day, which feels fair and is almost always wrong. Your grade in each class depends on how much the final is worth, what you already understand, and how much of the material you can actually retrieve without notes.",
            ]},
            {"h2": "Day one: build the list, not the flashcards",
            "paragraphs": [
                "Spend the first two hours of the week making a list instead of studying. For each class, write down the final's weight, the topics it covers, and a one-word confidence rating for each topic. You are looking for the gap between how much a topic is worth and how well you know it.",
            ], "list": [
                "Open your gradebook and write down every class's current grade and the final's weight.",
                "List the units or chapters the final covers. If the teacher gave a review sheet, that is your list.",
                "Mark each unit green (can explain it), amber (recognise it), or red (would fail a quiz on it today).",
                "Count the red and amber units per class and rank the classes by that count multiplied by the final's weight.",
            ]},
            {"h2": "Days two to five: one subject per block, retrieval first",
            "paragraphs": [
                "Study the highest-ranked class in your first block of the day, when attention is cheapest. Within a subject, do not reread the chapter. Close the book, write down everything you remember about the unit, then open it and check. The gap between what you wrote and what is on the page is the only study list that matters.",
                "Two hours of that beats five hours of highlighting, and it is uncomfortable enough that most students avoid it. That discomfort is the point: retrieval is harder than recognition, which is exactly why it works. If a unit comes back easily twice in a row, it is done. Move its time to a red unit in another class.",
            ], "list": [
                "Block one (60 to 90 minutes): the class with the most red units.",
                "Block two: a different subject. Switching forces your brain to reload the context instead of coasting.",
                "Block three: practice problems or past questions, timed, with notes closed.",
                "End each block by writing the three things you still cannot explain. That is tomorrow's warm-up.",
            ]},
            {"h2": "Day six: full practice, no notes",
            "paragraphs": [
                "Take one full practice test or review packet per class under exam conditions: timed, phone away, no notes. Score it honestly. This is the day that tells you whether the week worked, and it is the day most people skip because it feels bad to find out.",
                "Use the results to make a short list (three to five specific gaps per class) and spend the evening on those rather than on a fresh pass through everything.",
            ]},
            {"h2": "Day seven: light review, early night",
            "paragraphs": [
                "The night before a final is for skimming your own notes, rereading the gaps list, and sleeping. New material at this point mostly adds anxiety. If you must study, do twenty minutes of retrieval on the units you marked red, then stop.",
                "Sleep is not a reward for finishing; it is part of the studying. Memory consolidation happens overnight, and a student who slept seven hours outperforms a better-prepared student who did not.",
            ]},
            {"h2": "Keeping the week from collapsing",
            "paragraphs": [
                "A seven-day plan survives only if it is visible and adjustable. Put every block on a calendar with a start time, and expect to move two of them. The follow-through features in a planner help here for a mundane reason: a plan you can see is a plan you can repair, and repair beats restarting.",
                "If you use IntelliPlan, connect your classes first so the finals and assignments already on your list are in the same place as the study blocks you add. The scheduler can lay out the week from your available hours, and the reschedule actions let you push a block without rebuilding the whole plan when something goes wrong.",
            ]},
        ],
        "related": ["how-to-make-a-study-schedule-that-works", "active-recall-vs-rereading", "how-to-stop-procrastinating-on-homework"],
    },
    {
        "slug": "how-to-make-a-study-schedule-that-works",
        "kicker": "Planning",
        "title": "How to Make a Study Schedule That Actually Works",
        "meta_title": "How to Make a Study Schedule That Works | IntelliPlan",
        "description": "Most study schedules fail in the first week. Here is a simple way to build one around real hours, real deadlines, and real energy.",
        "date": "2026-11-10",
        "reading_time": 6,
        "sections": [
            {"paragraphs": [
                "Almost everyone has made a study schedule. Almost nobody has kept one for a month. The schedules that fail are usually not too ambitious. They are too abstract. A block that says \"study biology\" is a wish; a block that says \"redo questions 4 to 12 from the cell division worksheet, 7pm to 7:40\" is a plan.",
                "A schedule that survives contact with a real week has four properties: it starts from the hours you actually have, it names a specific output for each block, it leaves slack, and it can be repaired without being rebuilt.",
            ]},
            {"h2": "Start with the hours you really have",
            "paragraphs": [
                "Write down your week as it is: school, practice, work, commute, meals, and the time you genuinely spend decompressing. What is left is your study budget. Most students overestimate it by five to ten hours because they plan for an ideal week rather than the one they keep living.",
            ], "list": [
                "Count the free hours in each weekday evening honestly, in 30-minute units.",
                "Add weekend hours separately. Do not plan every weekend hour; you will not use them.",
                "Take 20 percent off the total as slack. If you have 20 hours, plan 16.",
            ]},
            {"h2": "Give every block a finish line",
            "paragraphs": [
                "Each block needs a verb and an object: solve, rewrite, recite, outline, redraw. \"Review\" and \"go over\" are not verbs in this sense, because you cannot tell when you are done. A finish line also makes a block cancellable. You know exactly what moved to tomorrow.",
            ], "list": [
                "Weak: study history for an hour.",
                "Better: write a timeline of the five causes of the war from memory, then check it against the notes.",
                "Weak: practise maths.",
                "Better: redo the eight questions I got wrong on the last quiz, closed book, then check.",
            ]},
            {"h2": "Plan by deadline pressure, not by subject order",
            "paragraphs": [
                "If you plan Monday for maths because Monday is maths day, you will spend the week reacting instead of preparing. Sort the work by when it is due and how much it counts, then place the highest-pressure items in your best hours. Everything else fills in around them.",
                "This is the same logic a priority score uses: due date, point value, and how much of the course grade the item carries. You can do it on paper in two minutes. The advantage of doing it inside a planner is that it re-sorts itself when a due date moves, which on a normal week happens three or four times.",
            ]},
            {"h2": "Protect one block from the week",
            "paragraphs": [
                "Something will go wrong: a shift gets added, a friend needs help, a paper takes longer. If every block is equally important, the first bad day destroys the plan. Pick one anchor block per day (usually the highest-value task) and treat the rest as movable.",
                "The follow-through engine in IntelliPlan exists for this exact moment. When a day falls apart you tell it what happened (\"less time today\", \"I am behind\", \"not today\") and it repairs the plan around the constraint instead of asking you to start over.",
            ]},
            {"h2": "Review the schedule once a week, not every day",
            "paragraphs": [
                "Ten minutes on Sunday: what got done, what moved, what you avoided. The avoided items are the useful signal. If the same task has moved three weeks running, the block is too big or the task is unclear. Split it in half and schedule the first half only.",
                "A schedule is a hypothesis about your week. The point is not to follow it perfectly; it is to notice quickly when it is wrong and change the plan rather than abandoning it.",
            ]},
        ],
        "related": ["time-blocking-for-students", "how-to-study-for-finals-in-one-week", "how-to-prioritize-assignments"],
    },
    {
        "slug": "time-blocking-for-students",
        "kicker": "Study skills",
        "title": "Time Blocking for Students: A Practical Guide",
        "meta_title": "Time Blocking for Students: A Practical Guide | IntelliPlan",
        "description": "How to time-block a school week around real deadlines, with a simple template and the mistakes that make blocking fail.",
        "date": "2026-11-06",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Time blocking is the practice of giving every hour a job in advance. Not a to-do list that says what should happen eventually, but a calendar that says what happens at 7pm. For students it solves a specific problem: the free hour between practice and dinner is invisible until it is gone.",
                "It is also the technique most likely to be abandoned in week two, because the usual version asks you to predict your week perfectly. You do not need to.",
            ]},
            {"h2": "Block outcomes, not activities",
            "paragraphs": [
                "A block called \"study\" will absorb anything: three tabs, a snack, a conversation. A block called \"finish the lab write-up through the graph section\" has a shape. When the block ends you either got there or you did not, and both answers are useful.",
                "If you cannot name an outcome, the task is not ready to be scheduled. Break it until you can.",
            ]},
            {"h2": "Use three block lengths",
            "paragraphs": [
                "Most school work fits one of three shapes, and matching the block to the shape stops you from over-committing.",
            ], "list": [
                "25 minutes: flashcards, vocabulary, a single problem set, rereading your own notes. Good for the gaps in a school day.",
                "50 minutes: a full practice section, an essay outline, a chapter with retrieval at the end. The default study block.",
                "90 minutes: a practice exam, a lab report, a draft. Requires a real break afterwards.",
            ]},
            {"h2": "Block the day, not just the evening",
            "paragraphs": [
                "The most reliable blocks are the ones attached to something that already happens. Ten minutes after you sit down in second period. Twenty minutes on the bus. Thirty minutes between the end of school and practice. These are low-willpower blocks because the transition already exists.",
                "Evening blocks are the ones that compete with everything else, so use them for the work that needs a desk and quiet, not for the work you could have done at lunch.",
            ]},
            {"h2": "Leave a gap between blocks",
            "paragraphs": [
                "Back-to-back 50-minute blocks look efficient and feel like a treadmill. Leave ten minutes between them. That gap absorbs the overrun, the bathroom, the message you need to answer, and it means a late start does not cascade through the evening.",
                "The same rule applies across subjects: do not schedule maths immediately after a maths block. Switching subjects gives the first one time to consolidate and stops the second from feeling like more of the same.",
            ]},
            {"h2": "When the block does not happen",
            "paragraphs": [
                "Two things go wrong: the block was unrealistic, or the day was. Distinguish them. If you consistently cannot start at 7pm because that is when your household eats, move the block. If you could have started but did not, the block is probably too big or too vague.",
                "The repair matters more than the plan. A blocked week that gets rebuilt once is normal; a blocked week that gets abandoned after two missed blocks is the failure mode to design against. IntelliPlan's scheduler does the rebuilding for you when a day changes, which is the part most paper systems make you do by hand at the worst possible moment.",
            ]},
        ],
        "related": ["how-to-make-a-study-schedule-that-works", "pomodoro-technique-for-students", "how-to-stop-procrastinating-on-homework"],
    },
    {
        "slug": "pomodoro-technique-for-students",
        "kicker": "Focus",
        "title": "Does the Pomodoro Technique Work for Students?",
        "meta_title": "Does the Pomodoro Technique Work for Students? | IntelliPlan",
        "description": "What the 25/5 timer actually does, when it helps, when it hurts, and how to adapt it for homework, essays, and exam prep.",
        "date": "2026-11-02",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "The Pomodoro Technique is the best-known productivity method in the world and one of the least examined. Twenty-five minutes of work, five minutes of break, repeat. It is genuinely useful, but not for the reason most students think, and it fails in predictable ways.",
                "The timer is not magic. What it does is make starting cheap and stopping deliberate. A task that would otherwise run until you are exhausted becomes a bounded interval you can agree to.",
            ]},
            {"h2": "Why it works",
            "paragraphs": [
                "Two mechanisms. First, a 25-minute commitment is small enough that refusing it feels unreasonable, which gets you past the initiation problem. Second, a fixed stop time prevents the slow attrition where a two-hour session produces forty minutes of work and two hours of fatigue.",
                "There is also an honest accounting effect: at the end of four pomodoros you know exactly how much time you spent, which makes tomorrow's planning less fictional.",
            ]},
            {"h2": "Where 25 minutes is wrong",
            "paragraphs": [
                "Anything with a long warm-up is a bad fit. A maths problem set, an essay draft, or a practice exam needs 40 to 90 minutes before you are fully in it; interrupting at 25 forces you to pay the warm-up cost four times. Use longer blocks for those.",
            ], "list": [
                "Use 25/5 for: flashcards, vocabulary, note review, single problems, reading with questions.",
                "Use 50/10 for: problem sets, lab write-ups, revision notes, timed practice sections.",
                "Use 90/20 for: essays, practice exams, anything that requires holding a large structure in your head.",
            ]},
            {"h2": "The break is part of the method",
            "paragraphs": [
                "The five-minute break is not permission to check your phone. Scrolling is not rest. It loads new information into the same system you are trying to give a break. Stand up, walk, drink water, look out a window. If you want to be strict about it, leave the phone in another room during the block.",
                "After four intervals, take a real break of 20 to 30 minutes. That is the point where attention genuinely degrades and pushing through produces work you will rewrite.",
            ]},
            {"h2": "Make it survive a real evening",
            "paragraphs": [
                "The version that fails is the one where a missed timer ends the session. If a pomodoro gets interrupted at minute 18, do not restart the count. Finish the thought, mark it, and start the next one.",
                "It also helps to decide the task before starting the timer, not during the first interval. Choosing what to do is the expensive part; doing it inside the block wastes the block.",
            ]},
            {"h2": "A practical setup",
            "paragraphs": [
                "Pick one task with a finish line. Set a timer for a length that matches the task. Put the phone out of reach. Work until the timer, then stop even if you are close. The next block starts in five minutes and you will arrive with the thread still warm.",
                "IntelliPlan's focus sessions and the Focus Shield extension use the same shape: a planned block, a timer, and a distractor list that is blocked only while the block is running. That last part matters because it removes the decision to resist at the moment you are least able to make it.",
            ]},
        ],
        "related": ["time-blocking-for-students", "how-to-study-with-adhd", "how-to-stop-procrastinating-on-homework"],
    },
    {
        "slug": "how-to-stop-procrastinating-on-homework",
        "kicker": "Study skills",
        "title": "How to Stop Procrastinating on Homework (For Real)",
        "meta_title": "How to Stop Procrastinating on Homework | IntelliPlan",
        "description": "Procrastination is an emotion problem, not a time problem. Five changes that make starting easier than avoiding.",
        "date": "2026-10-29",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "People who procrastinate are not lazy and they are not bad at planning. They are usually trying to avoid a feeling: the task is boring, or confusing, or the standard they are holding themselves to is high enough that starting feels like failing.",
                "That is why advice like \"just start\" does not work. The barrier is not the first minute of work; it is the dread attached to it. The fix is to make the first step small enough that it does not trigger the dread, and to make the avoidance more visible than the task.",
            ]},
            {"h2": "Shrink the first step until it is almost silly",
            "paragraphs": [
                "Not \"write the essay\". Open the document and write the title. Not \"study chemistry\". Write the names of the three topics you would fail today. The step should take under two minutes and require no decision.",
                "This works because the expensive part of avoidance is the imagined whole. Once you are in the document, the next step is much cheaper than the first one was.",
            ]},
            {"h2": "Name the feeling, then act anyway",
            "paragraphs": [
                "Before you start, finish this sentence: \"I do not want to start because ___.\" Bored? Confused? Afraid it will not be good? Each answer points at a different fix. Confusion needs a smaller question. Fear of quality needs permission for a bad first draft. Boredom needs a timer and a reward.",
                "This takes fifteen seconds and is more useful than another productivity app, because it tells you which intervention you actually need.",
            ]},
            {"h2": "Make the work visible and the distraction expensive",
            "paragraphs": [
                "A task that lives in your head is easy to postpone. A task on a list with a due date and an estimated duration is harder to ignore. Put the next action somewhere you will see it: a planner, a sticky note on the laptop, the top of tomorrow's schedule.",
                "Then raise the cost of the alternative. Phone in another room, not face-down on the desk. Block the sites you open without deciding to. The point is not discipline; it is removing the moment where you have to choose.",
            ]},
            {"h2": "Use a start ritual, not motivation",
            "paragraphs": [
                "Motivation arrives after starting far more reliably than before it. Pick a short, repeatable ritual: same chair, same drink, same first action (open the planner, write the next three tasks, start the timer). After a week the ritual does part of the work.",
            ], "list": [
                "Open the plan and read the next task out loud.",
                "Put the phone out of reach.",
                "Set a 25-minute timer.",
                "Start with the smallest possible step, even if it is one sentence.",
            ]},
            {"h2": "Plan for the days it does not work",
            "paragraphs": [
                "Some days the resistance is too high. On those days, do the ten-minute version: one small piece, on purpose, and count it. A streak of small starts is worth more than a heroic session followed by a week of avoidance.",
                "If you are behind across several classes, the useful move is not to work harder on the same plan. It is to rebuild the plan around what is actually left. IntelliPlan's \"I am behind\" reschedule does exactly that, moving only what has to move and showing you the consequence before you accept it.",
            ]},
        ],
        "related": ["pomodoro-technique-for-students", "how-to-make-a-study-schedule-that-works", "how-to-catch-up-when-behind-in-school"],
    },
    {
        "slug": "how-to-catch-up-when-behind-in-school",
        "kicker": "Recovery",
        "title": "How to Catch Up When You Are Behind in School",
        "meta_title": "How to Catch Up When You Are Behind in School | IntelliPlan",
        "description": "A triage method for missing work: what to do first, what to ask your teacher, and how to rebuild a plan you can actually finish.",
        "date": "2026-10-25",
        "reading_time": 6,
        "sections": [
            {"paragraphs": [
                "Being behind is not one problem, it is three: missing work that still counts, material you did not learn, and a plan that no longer matches reality. Students usually attack the third by making a bigger plan, which fails within a week because the first two are still there.",
                "The way out is triage. Sort what is missing by how much it can still change your grade, then work in that order while you rebuild the plan around the hours you actually have.",
            ]},
            {"h2": "Step one: make the real list",
            "paragraphs": [
                "Open every class's gradebook and assignment list and write down what is missing or late. Not what you think is missing. What the gradebook says. The gap between those two lists is usually where the panic comes from.",
                "For each item, note three things: how much it is worth, whether it can still be submitted, and how long it will take. This is the only information that should decide what you do first.",
            ]},
            {"h2": "Step two: triage by recoverable points",
            "paragraphs": [
                "Sort by points still recoverable per hour of work. A 50-point project due next week beats a 5-point worksheet from three weeks ago, even though the worksheet is easier and feels better to finish.",
            ], "list": [
                "First: anything still open, worth a lot, and due soon.",
                "Second: material the next test depends on, because falling further behind is more expensive than the missing item.",
                "Third: small missing items that take under ten minutes each. Batch them into one block.",
                "Last: work that can no longer be submitted. Ask, then let it go.",
            ]},
            {"h2": "Step three: email the teacher once, properly",
            "paragraphs": [
                "One short message covering every class is worse than one short message per teacher. Say what is missing, what you plan to do, and by when, and ask the specific question you need answered, usually whether late work is still accepted and whether there is a penalty.",
                "Specific and short gets replies. Long and apologetic often does not. Do not promise a date you cannot hit; if you need two more days, ask for two more days.",
            ]},
            {"h2": "Step four: rebuild the plan from what is left",
            "paragraphs": [
                "Now make a plan from the current list, not from the plan you had three weeks ago. Put the highest-value recoverable items in your best hours, batch the small ones, and leave one block empty for the thing you have not noticed yet.",
                "This is where a planner earns its keep: the work is already imported, so the rebuild is a matter of rearranging rather than retyping. If you use IntelliPlan, the \"I am behind\" action is built for this exact state. It re-plans the remaining deadlines instead of assuming the old schedule still holds.",
            ]},
            {"h2": "Step five: protect the next two weeks",
            "paragraphs": [
                "Catching up usually costs sleep, and sleep debt makes the next week worse. Cap the recovery at a sustainable level: one extra hour on weekdays, one longer block on the weekend, and a hard stop time. A recovery plan you can run for two weeks beats a heroic week that leaves you further behind.",
                "If the deficit is bigger than two weeks, the honest answer is that something has to be dropped or renegotiated. An extracurricular, a shift, a class. That conversation is uncomfortable and cheaper than failing quietly.",
            ]},
        ],
        "related": ["how-to-prioritize-assignments", "how-to-stop-procrastinating-on-homework", "how-to-make-a-study-schedule-that-works"],
    },
    {
        "slug": "how-to-study-with-adhd",
        "kicker": "Focus",
        "title": "How to Study With ADHD: Systems That Reduce Friction",
        "meta_title": "How to Study With ADHD: Systems That Work | IntelliPlan",
        "description": "Practical study systems for ADHD brains: external memory, short starts, body doubling, and plans that survive a bad day.",
        "date": "2026-10-21",
        "reading_time": 6,
        "sections": [
            {"paragraphs": [
                "Most study advice assumes a brain that remembers intentions, notices time passing, and can hold a boring task in place. If yours does not reliably do those things, the advice is not wrong so much as unusable. The fix is not more discipline; it is systems that do the remembering, the noticing and the holding for you.",
                "These are the changes that tend to matter most, in rough order of impact. None of them require a diagnosis to be useful.",
            ]},
            {"h2": "Get it out of your head immediately",
            "paragraphs": [
                "Anything held only in working memory will be lost at the first interruption, and school is one long interruption. Write it down the moment it appears. In a planner, on a note, in a message to yourself. The point is not organisation yet; it is capture.",
                "Then make the capture point visible. One list, one place, checked at the same two moments every day. Multiple lists recreate the problem you were solving.",
            ]},
            {"h2": "Start with a step that takes ninety seconds",
            "paragraphs": [
                "Task initiation is the expensive part, and a big task is expensive before you have written a single word. Shrink the entry point until it is almost trivial: open the file, write the heading, copy the question onto the page. Momentum is easier to borrow than to generate.",
            ], "list": [
                "Not: write the history essay.",
                "Yes: open the document, paste the prompt, write one sentence of the thesis.",
                "Not: study for the test.",
                "Yes: write the five topics from the review sheet on a blank page.",
            ]},
            {"h2": "Make time visible",
            "paragraphs": [
                "Time blindness is not fixed by willpower. Use a visible timer, not a clock you have to interpret. A countdown on the screen next to you does two jobs: it shows that the block is finite, and it shows when it ends without you having to check.",
                "Body doubling helps for the same reason. Working next to someone (in person, on a video call, or in a study room) externalises the structure you are trying to hold on your own.",
            ]},
            {"h2": "Design the environment, not the intention",
            "paragraphs": [
                "Intentions are cheap and unreliable. Environments are expensive and reliable. Put the phone in another room rather than promising not to look at it. Leave the textbook open to the right page. Block the sites that catch you, only while you are working.",
                "A blocklist that runs during a planned study session is better than one that runs all day, because an all-day block is one you will turn off by Wednesday.",
            ]},
            {"h2": "Plan for the bad day in advance",
            "paragraphs": [
                "A plan that only works on a good day is not a plan. Decide now what the minimum version looks like: ten minutes of flashcards, one problem, a single paragraph. Doing the minimum keeps the habit and the context alive, and the next day starts from somewhere rather than from zero.",
                "When the whole week has gone sideways, rebuild rather than restart. That is a mechanical operation. See what is left, reorder it by what counts, and re-place it in the hours that remain. IntelliPlan's reschedule actions are built around that idea, so the repair costs a minute instead of an evening.",
            ]},
            {"h2": "Use retrieval you can tolerate",
            "paragraphs": [
                "Rereading feels productive and mostly is not. Flashcards, blank-page recall and practice questions work better, but they are also more uncomfortable, which is why they get skipped. Make them shorter: five cards, not fifty. One practice question, then stop. Small and repeated beats large and avoided.",
            ]},
        ],
        "related": ["pomodoro-technique-for-students", "how-to-stop-procrastinating-on-homework", "active-recall-vs-rereading"],
    },
    {
        "slug": "how-to-take-better-notes-in-class",
        "kicker": "Notes",
        "title": "How to Take Better Notes in Class (That You Will Actually Use)",
        "meta_title": "How to Take Better Notes in Class | IntelliPlan",
        "description": "Notes are only useful if they help you remember later. What to write, what to skip, and how to review notes in ten minutes.",
        "date": "2026-10-17",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Most class notes are transcripts. They record what the teacher said in the order it was said, which is a good way to have a record and a poor way to learn. The notes that help are the ones written for the person who will read them two weeks later, half-remembering the topic.",
                "That person needs three things: the structure of the idea, the parts that were not obvious, and a way to test themselves. Everything else is optional.",
            ]},
            {"h2": "Write the structure first",
            "paragraphs": [
                "Before the detail, capture the shape: what question is this lesson answering, and what are the three or four pieces of the answer. A page with headings you can scan beats two pages of continuous sentences, and it is faster to write during a lecture because you are noting the frame rather than every word.",
            ]},
            {"h2": "Note the confusion, not just the content",
            "paragraphs": [
                "A question mark in the margin is worth more than a paragraph you copied without understanding. Mark the moment you stopped following, write the question you would ask, and move on. Reviewing those marks is the highest-value ten minutes you will spend on the subject.",
                "The same applies to worked examples. Copy the steps, then add one line in your own words saying why the second step was allowed. That line is the note; the steps are already in the textbook.",
            ]},
            {"h2": "Do not copy what you can look up",
            "paragraphs": [
                "Definitions, dates and formulas live in the book, the slides and the internet. Writing them down feels like studying and mostly is not. Spend the ink on connections, exceptions, and the teacher's emphasis. The parts that are not in the reading.",
            ]},
            {"h2": "Review in ten minutes, not two hours",
            "paragraphs": [
                "The day after the lesson, spend ten minutes on the notes: cover the page, say the main idea out loud, then check. Add one line about what you got wrong. That single pass is worth more than rereading the notes four times before the test, because it is retrieval rather than recognition.",
                "If you are using IntelliPlan's Study and Learn, you can upload the notes or paste them and get flashcards and practice questions from them. The useful part is not the generation; it is that reviewing becomes a question you can answer rather than a page you have to reread.",
            ]},
            {"h2": "Keep one place for everything",
            "paragraphs": [
                "Notes split across three apps and a paper notebook are notes you will not review. Pick one home for class notes and one for tasks. The class note is the archive; the planner holds the actions you extracted from it. The question to ask, the chapter to reread, the problem set to redo.",
            ]},
        ],
        "related": ["cornell-note-taking-method", "active-recall-vs-rereading", "how-to-study-for-finals-in-one-week"],
    },
    {
        "slug": "active-recall-vs-rereading",
        "kicker": "Study science",
        "title": "Active Recall vs Rereading: What the Evidence Says",
        "meta_title": "Active Recall vs Rereading: What Works | IntelliPlan",
        "description": "Rereading feels effective and mostly is not. How retrieval practice works, how to do it in ten minutes, and what it changes.",
        "date": "2026-10-13",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Ask a student how they study and most will describe reading: the chapter, the notes, the slides, again. It is the default because it is comfortable and because fluency (the feeling that the material is familiar) is easy to mistake for knowing it.",
                "Retrieval practice, usually called active recall, replaces reading with producing. Close the book and write or say what you remember. It feels worse and works better, and the size of the difference is not small.",
            ]},
            {"h2": "Why rereading fails",
            "paragraphs": [
                "Rereading strengthens recognition, not recall. You get better at recognising the page, which is exactly the skill a test does not measure. The information is in front of you the whole time, so your brain never has to reconstruct it.",
                "There is also an illusion of competence. Each pass feels easier, and the ease is read as mastery. Then the test asks you to produce the idea from nothing and the page you have read four times is not there.",
            ]},
            {"h2": "How to do it",
            "paragraphs": [
                "The method is deliberately unglamorous. Read a section once, close it, write everything you can remember on a blank page, then reopen and check. The difference between your page and the source is your study list.",
            ], "list": [
                "Turn headings into questions before you read, and answer them afterwards without looking.",
                "Use flashcards for facts, but say the answer out loud before flipping.",
                "Do practice questions with notes closed, then mark them honestly.",
                "Explain the topic to an imaginary classmate in plain language, out loud.",
            ]},
            {"h2": "Make it sustainable",
            "paragraphs": [
                "Retrieval is effortful, and effort is why students abandon it after two days. Keep sessions short: ten to twenty minutes of recall is a real session. Interleave topics so you are not doing the same kind of question for an hour. Stop while it still feels manageable.",
                "Spacing matters as much as the method. Three short recall sessions across a week beat one long one the night before, because each session starts from partial forgetting, which is the condition retrieval works best in.",
            ]},
            {"h2": "How to know it is working",
            "paragraphs": [
                "The signal is not that studying feels easier. Retrieval practice feels harder than rereading precisely because it is doing something, so a session that feels smooth is a session worth questioning.",
                "The real signal is what happens when you sit down a week later: can you produce the idea before you open anything? A second signal is transfer. Can you use the idea in a question phrased differently from the one in your notes? If yes to both, the method is working even if the session felt bad.",
            ]},
            {"h2": "Where tools help",
            "paragraphs": [
                "The bottleneck in retrieval practice is making the questions. Flashcards from your own notes, generated as a first draft and then edited, remove most of that cost. IntelliPlan's Study and Learn turns notes or PDFs into flashcards and practice questions, and the spaced repetition scheduler brings them back at intervals instead of all at once.",
                "The caveat is the usual one: generated questions are a starting point. Delete the ones that are trivial, and rewrite the ones that do not match how your teacher asks.",
            ]},
        ],
        "related": ["spaced-repetition-for-students", "how-to-take-better-notes-in-class", "how-to-study-for-finals-in-one-week"],
    },
    {
        "slug": "spaced-repetition-for-students",
        "kicker": "Study science",
        "title": "Spaced Repetition for Students: A Simple Explanation",
        "meta_title": "Spaced Repetition for Students, Explained | IntelliPlan",
        "description": "Why spacing beats cramming, what the forgetting curve means for your week, and how to run spaced review without a complex system.",
        "date": "2026-10-09",
        "updated": "2026-11-18",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Spaced practice means revisiting material in separate sessions. For example, recall the phases of mitosis on Monday and revisit the ones you missed on Wednesday. Practice recalling the answer before you look it up.",
                "A review by Cepeda and colleagues found that the gap that produced the best later recall depended on how long people needed to remember the material. Dunlosky and colleagues rated both practice testing and distributed practice highly across several kinds of material and assessment.",
            ], "links": [
                {"url": "https://pubmed.ncbi.nlm.nih.gov/16719566/", "label": "Cepeda et al. (2006), quantitative review of distributed practice"},
                {"url": "https://doi.org/10.1177/1529100612453266", "label": "Dunlosky et al. (2013), review of learning techniques"},
            ]},
            {"h2": "How long should a study block be?",
            "paragraphs": [
                "Give each block a finish you can recognize. Ten minutes might fit a short vocabulary review. A multi-step practice problem might need 40 minutes so you can solve it and check the reasoning. These are starting points you can adjust from your own experience.",
                "For a Friday quiz, try recalling the core ideas on Monday, applying them to new questions on Wednesday, and revisiting the ones you missed on Thursday. Set each session long enough to answer and check the questions you chose.",
            ]},
            {"h2": "What belongs in a spaced system",
            "paragraphs": [
                "Spacing is best for things you need to recall exactly: vocabulary, formulas, definitions, dates, conjugations, labelled diagrams. It is a poor fit for essay structure or multi-step problems, which need practice in context rather than isolated recall.",
            ], "list": [
                "Good: 40 Spanish verbs, the quadratic formula, the stages of mitosis, key dates.",
                "Bad: how to structure a DBQ, a full physics derivation, a thesis statement.",
            ]},
            {"h2": "Keep the deck small",
            "paragraphs": [
                "The failure mode is a deck of 600 cards made in one enthusiastic evening, reviewed twice, then avoided. Start with 20 cards for the unit you are currently in. Add as you go. A deck you review daily at 15 cards beats a perfect deck you never open.",
                "Delete cards that are ambiguous, trivial, or duplicated. A card that takes more than a few seconds to understand is a card that needs splitting.",
            ]},
            {"h2": "Fit it into a school week",
            "paragraphs": [
                "Spaced review is a filler task, not a main block. Ten minutes on the bus, five minutes before class, a short session after dinner. It works because it is frequent and low-effort, not because it is long.",
                "IntelliPlan's flashcard system tracks mastery per card and schedules reviews automatically, which removes the bookkeeping. You can also import existing decks from Anki or Quizlet, so switching does not mean starting from zero.",
            ]},
            {"h2": "Spacing and cramming are not enemies",
            "paragraphs": [
                "If a test is in three days, space what you can and cram the rest. The goal is not purity; it is that the material you will need again (next unit, next year, the final) got at least two spaced reviews rather than one long night.",
            ]},
        ],
        "related": ["active-recall-vs-rereading", "how-to-memorize-vocabulary-fast", "how-to-prepare-for-ap-exams"],
    },
    {
        "slug": "cornell-note-taking-method",
        "kicker": "Notes",
        "title": "The Cornell Note-Taking Method, Without the Busywork",
        "meta_title": "The Cornell Note-Taking Method, Explained | IntelliPlan",
        "description": "How the Cornell system works, why the summary column matters, and a version that fits a fast lecture without slowing you down.",
        "date": "2026-10-05",
        "reading_time": 4,
        "sections": [
            {"paragraphs": [
                "Cornell notes divide a page into three parts: a narrow left column for cues and questions, a wide right column for notes, and a strip at the bottom for a summary. It has survived for decades because the layout forces two things students otherwise skip. Turning notes into questions, and writing what the page was about.",
                "The common complaint is that it is slow during a lecture. It is, if you fill all three sections live. The version that works fills the right column in class and the other two afterwards.",
            ]},
            {"h2": "How to set it up",
            "paragraphs": [
                "Rule a vertical line about a third of the way in, and a horizontal line near the bottom. That is the whole system. On a laptop, use a two-column table and a summary row.",
            ], "list": [
                "Right column, in class: the actual notes, as short as you can make them.",
                "Left column, after class: a question or cue for each chunk of notes.",
                "Bottom strip, after class: two or three sentences summarising the page.",
            ]},
            {"h2": "Why the left column is the point",
            "paragraphs": [
                "A cue turns a note into a question, and a question can be answered with the page covered. That is retrieval practice built into the format. Without the cues, Cornell notes are ordinary notes with extra lines.",
                "Write cues that start with why, how, or what happens if. Those force explanation instead of recognition.",
            ]},
            {"h2": "The summary is a comprehension test",
            "paragraphs": [
                "If you cannot write three sentences about the page, you did not understand it well enough to take useful notes on it. That is uncomfortable and it is the value. Write the summary badly, then fix it after the reading or the next lesson.",
            ]},
            {"h2": "Common mistakes",
            "paragraphs": [
                "Three failures account for most abandoned Cornell pages. Writing the cues during the lecture, which splits attention and produces vague questions. Copying full sentences into the right column, which turns the page back into a transcript. And skipping the summary because the lesson ended and the bell rang.",
                "Fix the first by leaving the left column empty until after class. Fix the second by writing fragments instead of sentences. You are the only reader. Fix the third by treating the summary as a two-minute task you do before you close the notebook, not homework you will definitely come back to.",
            ]},
            {"h2": "Making it stick",
            "paragraphs": [
                "Review the left column only. Cover the right side, answer each cue out loud, then check. Five minutes per page, and you have a practice test written in your own handwriting.",
                "If you would rather not maintain the paper version, the same structure works digitally: paste your notes into IntelliPlan's Study and Learn, generate questions, and use those as the cue column. The layout is not the method; the questions are.",
            ]},
        ],
        "related": ["how-to-take-better-notes-in-class", "active-recall-vs-rereading", "how-to-study-for-finals-in-one-week"],
    },
    {
        "slug": "how-to-memorize-vocabulary-fast",
        "kicker": "Languages",
        "title": "How to Memorize Vocabulary Fast (and Keep It)",
        "meta_title": "How to Memorize Vocabulary Fast | IntelliPlan",
        "description": "A vocabulary routine that beats rereading a list: small batches, retrieval, spacing, and using the words in sentences.",
        "date": "2026-10-01",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "A vocabulary list is a test you have already seen the answers to, which is why reading it repeatedly feels like learning and produces almost nothing. The words that stick are the ones you have retrieved from memory, spaced out over days, and used in a sentence you made yourself.",
                "That is the entire method. The rest is details about batch size, order, and what to do with words that refuse to stick.",
            ]},
            {"h2": "Work in batches of ten",
            "paragraphs": [
                "Twenty new words at once is a list. Ten is a session. Learn ten to the point of recall (not recognition) before adding more. If you have forty words for Friday, that is four sessions across four days, not one long one on Thursday.",
            ]},
            {"h2": "Retrieve, do not reread",
            "paragraphs": [
                "Look at the English, say the target word before checking. Then reverse it: look at the target word, say the meaning. Both directions matter, because recognition in one direction does not guarantee production in the other.",
                "Sort the batch into three piles as you go: knew it instantly, got it after a pause, did not know it. Only the last two piles get reviewed again today.",
            ]},
            {"h2": "Say it out loud",
            "paragraphs": [
                "Pronunciation is part of the memory. A word you have only seen is stored more weakly than one you have said, and silently mouthing it does not count. Say it, even quietly. For gendered or tonal languages, say the article or the tone with the word every time. Relearning it later costs more.",
            ]},
            {"h2": "Make one sentence per word",
            "paragraphs": [
                "A short sentence about your own life is remembered far better than an abstract definition. It does not need to be clever. \"Je dois rendre le devoir demain\" is worth more than a conjugation table because it gives the word a context and a use.",
            ]},
            {"h2": "Space the reviews",
            "paragraphs": [
                "Review the batch the next day, then in three days, then in a week. Each review should be a recall attempt with the list covered, not a reread. Words you miss go back into tomorrow's batch; words you get twice in a row move out.",
                "This is exactly what a spaced repetition scheduler automates. IntelliPlan's flashcards track mastery per card, so the words you keep missing come back sooner and the ones you know stop wasting your time.",
            ]},
            {"h2": "What to do with words that will not stick",
            "paragraphs": [
                "Some words resist every method. Before adding more repetitions, check whether the card is the problem. A word with three meanings, a near-synonym of a word you already know, or a phrase you have only ever seen in a list will keep failing for reasons repetition cannot fix.",
                "Fix the card instead: add the gender or the preposition it takes, put it in the sentence where you first met it, or pair it with a picture or a sound. If it still fails after a week, park it. A word you meet again in reading will come back with context attached, and that is often faster than a ninth review.",
            ]},
            {"h2": "Read the words in the wild",
            "paragraphs": [
                "Flashcards build the initial trace; reading builds the connections. Once a batch is at maybe 70 percent recall, find the words in a text (a graded reader, a news article, a subtitle track) and notice them. Meeting a word in context is what turns a card into vocabulary.",
            ]},
        ],
        "related": ["spaced-repetition-for-students", "active-recall-vs-rereading", "how-to-prepare-for-ap-exams"],
    },
    {
        "slug": "how-to-prepare-for-ap-exams",
        "kicker": "AP",
        "title": "How to Prepare for AP Exams Without Burning Out",
        "meta_title": "How to Prepare for AP Exams | IntelliPlan",
        "description": "A realistic AP exam plan: when to start, how to use practice tests, and how to manage four exams in two weeks.",
        "date": "2026-09-27",
        "reading_time": 6,
        "sections": [
            {"paragraphs": [
                "AP exams have two features that make them different from a normal test: they cover a whole year, and they all arrive in the same two weeks. That combination is why students who study hard all year still walk into May feeling unprepared.",
                "The fix is to treat the year as the preparation and the last six weeks as the rehearsal. You are not learning from scratch in April; you are finding out which parts of the course you cannot yet produce under time pressure.",
            ]},
            {"h2": "Start with the format, not the content",
            "paragraphs": [
                "Every AP exam has a shape: how many multiple-choice questions, how long you get, how many free-response questions, and how they are scored. Knowing the shape is worth real points before you know any more content, because it decides how you spend the exam.",
            ], "list": [
                "Find the exam description and the scoring guidelines for each course on the College Board site.",
                "Do one full practice section early (even badly) so the format stops being unknown.",
                "Learn the point structure of the free-response section. Partial credit is where most students leave marks behind.",
            ]},
            {"h2": "Back-plan from exam day",
            "paragraphs": [
                "Count backwards from the exam date and place one full practice test per course every two to three weeks, then weekly in the last month. Everything else fills in around those dates. Practice tests are the load-bearing part of the plan; content review is what you do between them.",
                "If two exams fall on the same day, plan for it in March, not in May. The College Board publishes the schedule a year ahead, and late testing exists but has its own cost.",
            ]},
            {"h2": "Review by weakness, not by chapter order",
            "paragraphs": [
                "After each practice test, list the topics you lost points on and why: did not know it, knew it but ran out of time, or knew it and misread the question. Those are three different problems with three different fixes.",
                "Rereading the textbook is the slowest possible response to that list. Targeted practice on the specific topic, followed by another attempt at the same style of question, is the fast one.",
            ]},
            {"h2": "Practise under the real clock",
            "paragraphs": [
                "Most AP free-response disappointment is a pacing problem. Do timed sections with no notes and no pauses. If you cannot finish, practise the decision of which question to do first and when to abandon one.",
                "Write full answers, including the parts you think are obvious. Scorers award points for what is on the page, not for what you meant.",
            ]},
            {"h2": "Keep the other classes alive",
            "paragraphs": [
                "AP prep competes with regular homework, and the weeks before exams are also when projects are due. Put the practice tests in the calendar first, then let the planner fit the rest around them. Trying to hold both lists in your head is how the regular classes get neglected until it is expensive.",
                "IntelliPlan's priority view is useful here for an unglamorous reason: it puts a 100-point project and an AP practice test on the same ranked list, so the week reflects what actually matters instead of whichever app you happened to open.",
            ]},
            {"h2": "The last week",
            "paragraphs": [
                "No new material. Skim your own error lists, redo two or three free-response questions per subject, sleep. Students routinely lose more points to exhausted mistakes in the final week than they gain from one more chapter.",
            ]},
        ],
        "related": ["ap-study-planner", "how-to-study-for-finals-in-one-week", "spaced-repetition-for-students"],
    },
    {
        "slug": "how-to-study-for-math-tests",
        "kicker": "Maths",
        "title": "How to Study for a Math Test (Not Just Reread the Notes)",
        "meta_title": "How to Study for a Math Test | IntelliPlan",
        "description": "Maths is not a reading subject. A problem-first routine that finds your real gaps before the test does.",
        "date": "2026-09-23",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Maths is the subject where the gap between how students study and how they are tested is widest. You are tested on producing solutions to problems you have not seen, and most people study by reading worked solutions they have already seen. Those are different skills.",
                "The routine below is built on one rule: the page stays closed until you have attempted the problem.",
            ]},
            {"h2": "Start from a blank page",
            "paragraphs": [
                "Pick five problems from the section, close the book, and try them cold. Not to get them right. To find out what you actually remember. Mark each one: solved, stuck at the first step, or solved with the notes open.",
                "The middle category is the one that costs marks on tests. If you can only start with an example in front of you, you do not yet own the method.",
            ]},
            {"h2": "Classify your errors",
            "paragraphs": [
                "Every wrong answer belongs to a category, and the category decides the fix.",
            ], "list": [
                "Concept: you did not know what to do. Relearn the idea, then do two easier problems.",
                "Method: you knew the idea but picked the wrong technique. Practise choosing, by doing mixed problem sets rather than ten of the same kind.",
                "Execution: algebra slip, sign error, dropped term. Slow down one line at a time and write every step.",
                "Reading: you answered a different question than the one asked. Underline what is being asked before starting.",
            ]},
            {"h2": "Do mixed sets, not blocks of one type",
            "paragraphs": [
                "Textbook exercises group problems by type, which trains you to apply the method at the top of the page. Tests mix them, which is why students who can do every homework problem freeze on question three.",
                "Once you can do a section, mix it with the previous two sections and do ten random problems. Choosing the method is the skill being tested.",
            ]},
            {"h2": "Write the solution as if it were graded",
            "paragraphs": [
                "Maths teachers award method marks, and method marks disappear when the work is only in your head. Practise full written solutions: define variables, show the substitution, state the answer with units. It feels slow and it is what the exam wants.",
            ]},
            {"h2": "Use one practice test as a diagnostic",
            "paragraphs": [
                "If the teacher gives a review packet or a past test, treat it as data rather than homework. Do it closed-book, in one sitting, timed. Then mark it with the same care you would want a teacher to use, and sort the mistakes into the four categories above.",
                "That sorted list is your study plan for the remaining days, and it is usually shorter than the chapter list you started with. Students who do this routinely find that most of their lost marks come from two recurring errors rather than from not knowing the material at all.",
            ]},
            {"h2": "Use the last two days for errors only",
            "paragraphs": [
                "Two days before the test, stop doing new problems. Redo the ones you got wrong, cover the solution, and check. That list is a personalised prediction of the test.",
                "If you want a quick read on where you stand, the grade modeler in IntelliPlan will show what a given test score does to your course grade, which is useful for deciding how much this particular test is worth relative to everything else on your plate.",
            ]},
        ],
        "related": ["how-to-study-for-science-tests", "how-to-calculate-what-you-need-on-a-final", "active-recall-vs-rereading"],
    },
    {
        "slug": "how-to-study-for-science-tests",
        "kicker": "Science",
        "title": "How to Study for a Science Test: A Subject-Specific Plan",
        "meta_title": "How to Study for a Science Test | IntelliPlan",
        "description": "Science tests mix recall, diagrams and application. How to study each layer, in the order that actually raises your score.",
        "date": "2026-09-19",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "A science test usually contains three different exams stapled together: vocabulary you either know or do not, a diagram or process you have to reproduce accurately, and problems that apply the concept to a situation you have not seen. Studying only one of those is why a student can know the chapter and still get a C.",
                "Work through the three layers in order, because each one depends on the one before it.",
            ]},
            {"h2": "Layer one: the vocabulary and the definitions",
            "paragraphs": [
                "This is the cheap layer, and it is where spaced repetition belongs. Terms, units, symbols, and one-line definitions. Twenty cards a day for the week before the test is enough for most units.",
                "Do not memorise definitions word for word unless the teacher requires it. Aim for the ability to say what the term means and give an example.",
            ]},
            {"h2": "Layer two: the diagram or the process",
            "paragraphs": [
                "Most science units have one thing you must be able to draw or sequence: a cell, a cycle, a reaction pathway, a free-body diagram, a graph shape. Reproduce it from memory on a blank page, label it, then check against the book.",
                "Do it three times across three days. A diagram drawn once is a diagram you will half-remember, and half a diagram usually scores zero because the marks are on the labels.",
            ]},
            {"h2": "Layer three: application problems",
            "paragraphs": [
                "This is where the difference between an A and a B lives. Do not just read the worked examples. Cover them and attempt the same problem with different numbers. Then do the end-of-chapter problems that combine two ideas.",
                "If the test includes a lab or data question, practise describing a trend and justifying it with evidence from a graph. That sentence structure is worth marks and almost nobody practises it.",
            ]},
            {"h2": "Make a one-page sheet",
            "paragraphs": [
                "The day before, condense the unit onto one page: the five big ideas, the formulas with units, the diagram, and the three mistakes you keep making. The sheet is not for the test. Making it is the review. If it takes more than one page, you have not decided what matters yet.",
            ]},
            {"h2": "Use the review sheet as a diagnostic",
            "paragraphs": [
                "If the teacher provides a review packet, do not treat it as homework to complete. Treat it as a pretest: attempt it closed-book, mark it, and spend the remaining time only on what you missed. Completing it with notes open feels productive and tells you nothing.",
                "Science notes are also the easiest kind to turn into flashcards automatically: IntelliPlan's Study and Learn can generate questions from an uploaded PDF or pasted notes, which saves the twenty minutes of card-writing that usually stops people from starting.",
            ]},
        ],
        "related": ["how-to-study-for-math-tests", "spaced-repetition-for-students", "active-recall-vs-rereading"],
    },
    {
        "slug": "how-to-write-a-better-essay-in-less-time",
        "kicker": "Writing",
        "title": "How to Write a Better Essay in Less Time",
        "meta_title": "How to Write a Better Essay in Less Time | IntelliPlan",
        "description": "Most essay time is lost to staring at a blank page. A structure-first process that cuts drafting time without cutting quality.",
        "date": "2026-09-15",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Students rarely lose essay marks because they write slowly. They lose them because they start writing before they know what they are arguing, then spend three hours producing a draft that has to be reorganised. The time goes into rewriting a structure that was never decided.",
                "The process below front-loads the thinking so the drafting is fast. It works for a five-paragraph essay and for a ten-page paper.",
            ]},
            {"h2": "Write the thesis as a sentence with a because",
            "paragraphs": [
                "A thesis is a claim plus a reason. \"Social media harms teenagers\" is a topic. \"Social media harms teenagers because it replaces unstructured social practice with a metric-driven comparison\" is an argument you can defend and structure.",
                "If you cannot finish the because, you do not have an essay yet. You have a subject. Keep going until the sentence is specific enough that someone could disagree with it.",
            ]},
            {"h2": "Outline in claims, not topics",
            "paragraphs": [
                "An outline that says \"paragraph 2: body image\" is a table of contents. An outline that says \"paragraph 2: comparison metrics turn identity into a score, which is why appearance anxiety rises with use\" is a paragraph you can write in ten minutes.",
            ], "list": [
                "Three to five body claims, each one a full sentence that supports the thesis.",
                "Under each claim, the one piece of evidence you will use and the one sentence of analysis that connects it.",
                "A note on the strongest counterargument and where you will answer it.",
            ]},
            {"h2": "Draft ugly, then edit separately",
            "paragraphs": [
                "Drafting and editing use different parts of your attention, and doing them at the same time produces the slowest possible essay. Draft the whole thing without fixing sentences: brackets around the parts you will come back to, [CITATION], [BETTER WORD]. Then edit in one pass.",
                "The first draft should take about half the total time. If it is taking eighty percent, you are editing while drafting.",
            ]},
            {"h2": "Edit for the four things teachers mark",
            "paragraphs": [
                "Most rubrics reduce to four questions: is there a clear argument, is each claim supported, does the analysis explain the evidence rather than repeat it, and is it organised so a reader can follow. Read your draft once asking only those questions.",
            ], "list": [
                "Does every body paragraph start with a claim, not a fact?",
                "Is there a sentence after each quotation that says what it shows?",
                "Does the conclusion say something new, or restate the introduction?",
                "Is every source cited in the format the teacher asked for?",
            ]},
            {"h2": "Leave a gap before the final read",
            "paragraphs": [
                "If you can, finish the draft the day before it is due and proofread it the next morning. Errors you read past at midnight are obvious at 8am. If the deadline is tonight, read it aloud. Your ear catches missing words that your eye fills in.",
                "Giving yourself that gap is a scheduling problem more than a writing one. Putting the essay's draft milestone in your planner a day before the deadline, rather than the deadline itself, is the whole trick.",
            ]},
        ],
        "related": ["how-to-stop-procrastinating-on-homework", "time-blocking-for-students", "how-to-make-a-study-schedule-that-works"],
    },
    {
        "slug": "how-to-balance-ap-classes-and-sports",
        "kicker": "Balance",
        "title": "How to Balance AP Classes and Sports Without Falling Apart",
        "meta_title": "Balancing AP Classes and Sports | IntelliPlan",
        "description": "A season-long plan for athletes in demanding classes: protect sleep, use the small gaps, and plan around game days.",
        "date": "2026-09-11",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "A sport takes ten to twenty hours a week, and AP classes take the rest. The students who survive the season are not the ones with more discipline; they are the ones who stopped pretending they had evening hours available on practice days and built the week around that fact.",
                "Three things make the difference: protecting sleep, using the small gaps, and planning the season rather than the week.",
            ]},
            {"h2": "Accept the weekday reality",
            "paragraphs": [
                "On a practice day you may have ninety usable minutes after dinner, and that is if you eat fast. Planning four hours is how students end up choosing between sleep and homework at midnight.",
                "Put the ninety minutes where they belong: the one task that must happen today. Everything else goes to the weekend or to a study hall.",
            ]},
            {"h2": "Use the gaps that already exist",
            "paragraphs": [
                "School days contain more usable time than they feel like: a free period, the twenty minutes before practice, the bus ride, the ten minutes after you finish dressing. None of it is good for deep work, and all of it is good for flashcards, reading, and planning.",
            ], "list": [
                "Bus or car: flashcards, vocabulary, a chapter of reading.",
                "Locker room or waiting: review your notes from the day's classes.",
                "Study hall: the hardest assignment you have, because it is the only quiet block before evening.",
                "Before bed: five minutes planning tomorrow so the morning is not a decision.",
            ]},
            {"h2": "Plan the season, not just the week",
            "paragraphs": [
                "Game days and travel days are known in advance. Put them in the calendar at the start of the season and mark the days when you will have no usable evening. Then place the long assignments on the days around them, not on them.",
                "This is also how you avoid the week where a paper, a lab report, and an away game land on the same Thursday. You cannot move the game. You can move the paper's draft two days earlier if you see it in September.",
            ]},
            {"h2": "Protect sleep like a practice",
            "paragraphs": [
                "Sleep is where the season is won or lost. Athletes who sleep seven hours or fewer get injured more and recover more slowly, and the academic effect is just as reliable. A hard stop at a fixed time, even with work unfinished, is a better trade than a fourth hour of low-quality studying.",
                "If you are consistently choosing between sleep and homework, the schedule is wrong, not your character. Something has to move: an assignment started earlier, a study hall used better, or a class dropped at the semester.",
            ]},
            {"h2": "Keep one list",
            "paragraphs": [
                "Two lists (school and sport) become three when you forget one. Keep a single plan that shows practice, games, and deadlines together. IntelliPlan imports the school side and lets you add the rest, and the follow-through features assume days will get wrecked, which during a season they will.",
            ]},
        ],
        "related": ["time-blocking-for-students", "how-to-manage-homework-with-a-part-time-job", "how-to-make-a-study-schedule-that-works"],
    },
    {
        "slug": "how-to-manage-homework-with-a-part-time-job",
        "kicker": "Balance",
        "title": "How to Manage Homework With a Part-Time Job",
        "meta_title": "Homework With a Part-Time Job | IntelliPlan",
        "description": "Working students lose hours, not motivation. How to plan around shifts, protect the small blocks, and keep grades steady.",
        "date": "2026-09-07",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "A part-time job does not make you worse at school. It makes you time-poor in a way that most study advice ignores, because that advice assumes a free evening and a quiet house. Fifteen hours a week changes which techniques are available to you, not whether you can succeed.",
                "The plan has to start from the shifts. Everything else (assignments, revision, sleep) fits around a schedule that your employer controls.",
            ]},
            {"h2": "Put the shifts in first",
            "paragraphs": [
                "Before you plan any studying, block the shifts, the commute, and the recovery time after a closing shift. Then look at what is left. Students who plan the other way round build a beautiful week that evaporates the first time they get scheduled on a Tuesday.",
            ]},
            {"h2": "Do the hard work before the shift, not after",
            "paragraphs": [
                "Post-shift hours are low-quality hours. Use them for flashcards, rereading your notes, organising tomorrow's plan, or nothing. Put the essay, the problem set, and anything that needs sustained thought before work or on a day off.",
                "This is the single change that most working students report as the turning point: they stopped expecting 10pm to be productive and stopped feeling like failures when it was not.",
            ]},
            {"h2": "Use the fifteen-minute blocks",
            "paragraphs": [
                "Working students get more small blocks than large ones: before a shift, on a break, on the bus. A fifteen-minute block is enough for one retrieval session, ten flashcards, or a paragraph of an essay. It is not enough for a lab report, so do not schedule one there.",
            ]},
            {"h2": "Talk to your teachers early",
            "paragraphs": [
                "Most teachers will work with a student who tells them in September that they close on Sundays. Almost none will work with a student who tells them in December that they missed six assignments. Tell them once, briefly, and ask what the policy is for a shift that conflicts with a deadline.",
            ]},
            {"h2": "Protect one day off",
            "paragraphs": [
                "If you work five days and study seven, you will not last a semester. Pick one day a week with no shifts and no studying, and defend it. The rest of the plan is easier to keep when there is a visible end to the week.",
            ]},
            {"h2": "Keep the job from eating the term",
            "paragraphs": [
                "Two boundaries protect grades more than any study technique. First, cap your hours during exam weeks and tell your manager the dates in advance. Most will schedule around a school commitment if they hear about it in September rather than the night before. Second, do not volunteer for shifts you cannot afford to lose sleep over.",
                "If the job and the course load genuinely do not fit, that is information, not failure. Dropping to one shift a week for a month is usually cheaper than retaking a class, and it is a decision you can make deliberately instead of by missing deadlines until it is made for you.",
            ]},
            {"h2": "Let the plan absorb the chaos",
            "paragraphs": [
                "Shift changes are the normal case, not the exception. A plan you have to rebuild by hand every time is a plan you will abandon. IntelliPlan's reschedule actions are designed for exactly this: tell it the day got shorter and it moves what has to move instead of asking you to redo the week.",
            ]},
        ],
        "related": ["how-to-balance-ap-classes-and-sports", "time-blocking-for-students", "how-to-catch-up-when-behind-in-school"],
    },
    {
        "slug": "how-to-use-google-classroom-with-a-planner",
        "kicker": "Google Classroom",
        "title": "How to Use Google Classroom With a Study Planner",
        "meta_title": "Google Classroom With a Study Planner | IntelliPlan",
        "description": "Classroom shows coursework per class. A planner turns it into one ranked week. How to connect them and what to check after.",
        "date": "2026-09-03",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Google Classroom is good at distributing work and bad at telling you what to do next. Each class has its own stream and its own due dates, and the to-do list only shows what is assigned to you, not how much it is worth, how long it will take, or which of the four things due Friday matters most.",
                "Connecting Classroom to a planner turns that per-class view into a single week. Here is how the connection works and what to check once it is done.",
            ]},
            {"h2": "What gets imported",
            "paragraphs": [
                "IntelliPlan connects through Google OAuth, the same sign-in you use for Classroom itself. It reads your active courses and the coursework in them: titles, due dates, and the course each item belongs to.",
                "It does not post, comment, or change anything in Classroom. The connection is read-only, and you can disconnect it from Settings without touching your Google account.",
            ]},
            {"h2": "Connecting it",
            "paragraphs": ["The whole flow is a normal Google consent screen, so the important part is reading it rather than clicking through."], "list": [
                "Create a free IntelliPlan account and open Connect.",
                "Choose Google Classroom, then pick the Google account your school uses.",
                "Approve the read-only Classroom access on the consent screen.",
                "Return to the dashboard and check that each of your classes appears.",
            ]},
            {"h2": "Check three things after the import",
            "paragraphs": [
                "Most import problems are not failures; they are differences between how Classroom stores a date and how you think about it. Three checks catch nearly all of them.",
            ], "list": [
                "Classes you are not taking: archived or inactive courses should not appear. If one does, it is usually still active in Classroom.",
                "Assignments with no due date: Classroom allows them, and they will import without one. Give them a date yourself or they will never surface in a plan.",
                "Time zone: a due time of 11:59pm can land on the next day if the course or the account is set to another zone. Check one assignment you know the real deadline for.",
            ]},
            {"h2": "Turn the list into a week",
            "paragraphs": [
                "Once the work is imported, the useful move is not to look at it but to schedule it. Give the planner your real available hours and your preferred study time, and it will place the work into blocks, breaking long assignments into sittings rather than leaving a 90-minute block that nobody starts.",
                "Two things make the resulting schedule more realistic. First, keep your hours honest. Most students overestimate by five to ten hours a week. Second, add your fixed commitments (practice, work, rehearsal) before generating, because the planner can only avoid what it can see.",
            ]},
            {"h2": "Keep Classroom open, but stop checking it",
            "paragraphs": [
                "The point of the connection is that you no longer need to visit four class pages to find out whether something new appeared. Check the planner once in the morning and once after school. If a teacher adds something mid-day, it will be there at the next sync.",
                "One caveat worth knowing: Classroom is not the only place teachers put work. A paper handout, an email, or a verbal announcement still has to be added by hand. The planner is only as complete as everything you put into it, which is why manual tasks live on the same list.",
            ]},
            {"h2": "If you also use Canvas",
            "paragraphs": [
                "Many students have one teacher on Classroom and another on Canvas. IntelliPlan supports both at once, and the priority view merges them, which is the case where a single planner earns its keep most obviously.",
            ]},
        ],
        "related": ["how-to-use-canvas-with-a-study-planner", "how-to-use-schoology-assignments", "how-to-prioritize-assignments"],
    },
    {
        "slug": "how-to-use-schoology-assignments",
        "kicker": "Schoology",
        "title": "How to Keep Track of Schoology Assignments",
        "meta_title": "Keeping Track of Schoology Assignments | IntelliPlan",
        "description": "Schoology buries due dates inside courses. How to pull assignments into one list, and what to do when a course does not import.",
        "date": "2026-08-30",
        "reading_time": 4,
        "sections": [
            {"paragraphs": [
                "Schoology keeps a lot in the course stream and relatively little in a single place you can scan. There is a calendar and an upcoming panel, but both depend on teachers using the assignment tool consistently, and many do not.",
                "That is the core problem with any single LMS: your real workload is only as visible as the least consistent teacher on your schedule.",
            ]},
            {"h2": "The manual approach, and where it breaks",
            "paragraphs": [
                "The usual solution is a weekly pass: open each course, check the assignments and the materials folder, write down what is due, and repeat next week. It works, and it takes about twenty minutes per course per week if you are thorough.",
                "It breaks in two places. Material posted in an update rather than the assignment tool never makes it to the list, and a deadline that moves is only noticed if you happen to open that course again.",
            ]},
            {"h2": "Connecting Schoology",
            "paragraphs": [
                "IntelliPlan supports Schoology through its API, using an API key and secret from your account rather than your password. That is a deliberate design choice: a revocable key can be removed without changing the password you use everywhere else.",
                "In Schoology, the key lives in your account settings under API. Generate one, copy both values, and paste them into the Connect screen in IntelliPlan. Assignments and due dates then import into the same list as every other platform.",
            ]},
            {"h2": "What to check after importing",
            "paragraphs": [
                "Two things tend to go wrong on the first sync, and neither is a bug.",
            ], "list": [
                "Drafts and unpublished assignments: these should not appear. If something does show up that you cannot see in Schoology, it is usually unpublished work.",
                "Graded discussions and folder items: some teachers attach due dates to materials rather than assignments. Those may import as items without a clear type. Give them a time estimate yourself.",
            ]},
            {"h2": "Make the list useful",
            "paragraphs": [
                "An imported list is still just a list. The value comes from ranking it: due date, points, and how much the item counts toward the course grade. That is what turns twenty items into an order you can act on tonight.",
                "IntelliPlan scores priority across every connected source, so a Schoology quiz and a Canvas essay compete on the same terms. If you are juggling two platforms (which is common when a district uses one and a magnet program uses another) that is the difference between one list and two competing ones.",
            ]},
            {"h2": "When a course will not import",
            "paragraphs": [
                "Some districts restrict API access, and some courses are set up in ways the API does not expose. The fallback is manual entry, which is less convenient and still better than a second list you forget to check. Add the course's assignments by hand once a week in the same place as everything else.",
            ]},
        ],
        "related": ["how-to-use-google-classroom-with-a-planner", "how-to-use-canvas-with-a-study-planner", "how-to-prioritize-assignments"],
    },
    {
        "slug": "how-to-track-grades-without-checking-the-portal",
        "kicker": "Grades",
        "title": "How to Track Your Grades Without Checking the Portal Every Day",
        "meta_title": "Track Grades Without Checking Constantly | IntelliPlan",
        "description": "Refreshing the grade portal is anxiety, not information. What to check weekly instead, and which numbers actually matter.",
        "date": "2026-08-26",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Checking your grades five times a day feels like staying on top of things. It is usually the opposite: the portal updates when a teacher grades something, not when you do, so the refresh gives you no new information and a fresh dose of anxiety.",
                "The alternative is not to stop caring. It is to check less often, look at different numbers, and act on the ones you can still change.",
            ]},
            {"h2": "What the portal actually tells you",
            "paragraphs": [
                "A gradebook is a record of decisions already made. It is good at telling you what a missing assignment cost and bad at telling you what to do next. The first is history; the second is what a plan is for.",
                "It is also frequently wrong in the short term. Teachers grade in batches, categories are weighted unevenly, and a zero for an assignment you turned in yesterday can sit there for a week.",
            ]},
            {"h2": "Check once a week, on a fixed day",
            "paragraphs": [
                "Pick a day and time (Sunday evening works for most people) and do one pass across every class. Write down three things per class: the current grade, anything missing or flagged, and any category that is unexpectedly low.",
                "A weekly pass takes ten minutes and catches everything a daily refresh would, without the loop. If a grade changes mid-week and you cannot act on it, knowing sooner does not help.",
            ]},
            {"h2": "Look at categories, not the letter",
            "paragraphs": [
                "The letter is a summary; the categories are the levers. A class with homework worth 20 percent and tests worth 60 percent is not rescued by perfect homework. A class where participation is 15 percent and you have never spoken is a different problem than a low test average.",
            ], "list": [
                "Which category is dragging the grade down?",
                "Is that category still open, or finished?",
                "What is the next item in it worth, and when is it due?",
            ]},
            {"h2": "Model the change before you panic",
            "paragraphs": [
                "A low grade with three weeks left is a maths problem, not a verdict. Work out what a specific score on the next test would do to the course grade before deciding the class is lost. Students routinely assume a C is fixed when one strong test would move it to a B.",
                "IntelliPlan's grade modeler does that calculation for you, including across several classes at once, and the free final grade calculator on this site handles the single-class version.",
            ]},
            {"h2": "Act on the next item, not the last one",
            "paragraphs": [
                "Once you know which category matters, the only useful question is what the next graded item is and how to do it well. Everything else is commentary.",
                "That is also the healthiest version of grade tracking: it converts a number you feel bad about into a task with a date, which is something you can actually do something about.",
            ]},
        ],
        "related": ["how-to-calculate-what-you-need-on-a-final", "how-to-raise-a-b-in-a-class", "what-is-a-good-gpa"],
    },
    {
        "slug": "how-to-calculate-what-you-need-on-a-final",
        "kicker": "Grades",
        "title": "How to Calculate What You Need on a Final",
        "meta_title": "What You Need on a Final: The Formula | IntelliPlan",
        "description": "The exact arithmetic for a target grade, with worked examples, plus what to do when the number is impossible.",
        "date": "2026-08-22",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "The question is always the same: what do I need on the final to get the grade I want? The arithmetic is simple, the hard part is knowing which numbers to put in, and what to do when the answer is above 100.",
                "Here is the whole method, then the cases where it goes wrong.",
            ]},
            {"h2": "The formula",
            "paragraphs": [
                "Your course grade is the weighted average of what you have so far and the final. If the final is worth F percent, then your current work is worth 100 minus F percent.",
                "Required final score = (target grade − current grade × (1 − F)) ÷ F",
                "Where all grades are decimals: 87 percent is 0.87, a final worth 20 percent is 0.20.",
            ]},
            {"h2": "A worked example",
            "paragraphs": [
                "You have an 84 in the class, the final is worth 25 percent, and you want a 90.",
                "Required = (0.90 − 0.84 × 0.75) ÷ 0.25 = (0.90 − 0.63) ÷ 0.25 = 0.27 ÷ 0.25 = 1.08.",
                "You need 108 percent, which means the target is not reachable on the final alone. Better to know that now than after the exam.",
            ]},
            {"h2": "Where the numbers go wrong",
            "paragraphs": [
                "Three mistakes account for most of the bad answers.",
            ], "list": [
                "Using the unweighted average when categories are weighted. Check the syllabus, not the portal's summary.",
                "Forgetting that the final replaces a dropped test or counts in a category. Ask the teacher if the syllabus is unclear.",
                "Treating an in-progress grade as final. If three assignments are ungraded, your current grade will move before the final does.",
            ]},
            {"h2": "When the answer is above 100",
            "paragraphs": [
                "It means the target is not available through the final alone. There are still options: extra credit if the teacher offers it, a conversation about which assignment carries the most weight, or accepting a lower target and protecting the other classes instead.",
                "That last one is not defeat. Spending fifteen hours chasing an unreachable A in one class while a reachable B slips in another is the most common bad trade in exam season.",
            ]},
            {"h2": "The GPA version of the question",
            "paragraphs": [
                "The same arithmetic works one level up. If you know the likely grade in each course and the credit hours each course carries, you can work out what a strong final in one class does to your term GPA before you sit the exam.",
                "That is worth doing when two finals are on the same day and you cannot prepare equally for both. Comparing the GPA effect of an extra five hours in each class is a better use of the decision than comparing how much you like the subjects.",
            ]},
            {"h2": "Use the number to plan, not to worry",
            "paragraphs": [
                "Once you know you need a 92, you can decide how much time the final deserves relative to everything else that week. That is a scheduling decision, and it is much easier to make with a number than with a feeling.",
                "The final grade calculator on this site does the arithmetic for you, and IntelliPlan's grade modeler extends it to whole-course and GPA effects so you can compare several classes at once.",
            ]},
        ],
        "related": ["how-to-raise-a-b-in-a-class", "how-to-track-grades-without-checking-the-portal", "what-is-a-good-gpa"],
    },
    {
        "slug": "how-to-raise-a-b-in-a-class",
        "kicker": "Grades",
        "title": "How to Raise a B to an A (or a C to a B) This Term",
        "meta_title": "How to Raise a Grade This Term | IntelliPlan",
        "description": "The realistic levers for moving a grade: category maths, the next two graded items, and the conversations worth having.",
        "date": "2026-08-18",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Raising a grade is not about working more. It is about working on the items that are actually holding the grade down, which are usually fewer and more specific than they feel.",
                "This is a five-step process that works from the middle of a term. It works better than it sounds because most students skip step one.",
            ]},
            {"h2": "Step one: find the category that is costing you",
            "paragraphs": [
                "List every category in the syllabus with its weight and your current average in it. Multiply the gap between your average and your target by the weight. That number is how many grade points the category is costing you.",
                "A 70 percent in a category worth 10 percent costs you three points overall. A 78 in a category worth 40 percent costs you nearly nine. The second one is where the work is, even though the first one looks worse.",
            ]},
            {"h2": "Step two: ignore what is already closed",
            "paragraphs": [
                "A finished test cannot be changed. An open category with three homework assignments left can. Direct your effort at the categories with the most remaining weight, not the ones with the most missing work.",
            ]},
            {"h2": "Step three: win the next two graded items",
            "paragraphs": [
                "Momentum matters for grades and for how teachers see you. Pick the next two graded items in the expensive category and do them properly: on time, complete, and checked against the rubric. Two good items in a row often move a grade more than a heroic effort on something worth five points.",
            ]},
            {"h2": "Step four: have one conversation",
            "paragraphs": [
                "Ask the teacher a specific question: what would move my grade most in the next three weeks, and is there anything I can redo? Teachers respond to that far better than to a request for extra credit, and many have a redo or revision policy they do not advertise.",
                "Bring the category maths with you. \"I am at 78 in the 40 percent category and the next test is the last one before grades close\" is a conversation. \"Is there extra credit\" is a lottery ticket.",
            ]},
            {"h2": "Step five: model the target before you commit",
            "paragraphs": [
                "Before you spend three weeks on one class, check what the realistic ceiling is. If the best case is a B+ and another class is a reachable A, the time belongs in the second one. IntelliPlan's grade modeler will show the effect of a given score on the course grade so you can compare, and the grade calculators on this site handle single cases quickly.",
            ]},
            {"h2": "What does not work",
            "paragraphs": [
                "Rereading the textbook, asking for extra credit you cannot finish, and emailing the teacher at the end of the term. Also: raising one grade while letting two others fall, which is a net loss.",
            ]},
        ],
        "related": ["how-to-calculate-what-you-need-on-a-final", "how-to-track-grades-without-checking-the-portal", "what-is-a-good-gpa"],
    },
    {
        "slug": "how-to-build-a-college-application-timeline",
        "kicker": "College",
        "title": "How to Build a College Application Timeline (Junior and Senior Year)",
        "meta_title": "College Application Timeline | IntelliPlan",
        "description": "When to take tests, ask for recommendations, and write essays, laid out by month for junior and senior year.",
        "date": "2026-08-14",
        "reading_time": 6,
        "sections": [
            {"paragraphs": [
                "College applications are not hard because any one piece is difficult. They are hard because eight pieces have deadlines that are months apart and depend on each other, and nobody makes you a schedule until you build one.",
                "Here is a timeline that assumes a US application cycle and can be compressed if you start late.",
            ]},
            {"h2": "Junior year: tests and relationships",
            "paragraphs": [
                "The main junior-year job is getting the inputs ready so senior year is writing, not scrambling.",
            ], "list": [
                "Fall: take the PSAT if your school offers it; use the result to decide between SAT and ACT if you have a choice.",
                "Winter: take a first real SAT or ACT. A spring retake is normal; a first attempt in September of senior year is late.",
                "Spring: build the list, roughly 8 to 12 schools across reach, match and likely. Note which need SAT Subject tests, portfolios or auditions.",
                "Spring: ask two teachers who know you whether they would write a strong recommendation. Ask early, before the senior-year rush.",
                "Summer: visit or research the schools you can realistically apply to, and start the Common App essay if you want senior fall to be calm.",
            ]},
            {"h2": "Senior fall: the actual deadline season",
            "paragraphs": [
                "Early Action and Early Decision deadlines cluster around November 1, and Regular Decision around January 1. Work backwards from those dates, not from the calendar month.",
            ], "list": [
                "August: confirm your senior schedule; colleges look at it. Start the essay in earnest.",
                "September: fill in the application forms, request transcripts, and give recommenders the exact deadlines and a summary of your activities.",
                "October: essay drafts finished two weeks before the deadline. Financial aid forms open; the FAFSA opens in the fall and some aid is first-come.",
                "November: Early deadlines. Submit a week early so a portal problem is not a crisis.",
                "December: Regular Decision essays. Early results arrive mid-December.",
                "January: Regular deadlines. Confirm every school received every piece.",
            ]},
            {"h2": "The three things students underestimate",
            "paragraphs": [
                "Recommendation requests: teachers write dozens and are asked late. Ask in spring of junior year or the first week of senior year with a written summary of what you did in their class.",
                "Essay revisions: the first draft is not the essay. Plan three passes, including one where you cut 20 percent.",
                "The portal check: after submitting, every school has a portal where you confirm documents arrived. A missing transcript in December is common and fixable, if you look.",
            ]},
            {"h2": "Keeping it in one place",
            "paragraphs": [
                "Put every deadline in the same planner you use for school, with the draft milestones ahead of them. Essay draft, recommendation reminder, transcript request. Deadlines that live in a separate document are the ones that surprise you.",
                "If you are applying while carrying a full course load, the planning has to treat applications as coursework with hours attached. An essay is not a two-hour task, and a plan that assumes it is will fail the week it is due.",
            ]},
        ],
        "related": ["sat-study-schedule", "how-to-write-a-better-essay-in-less-time", "how-to-make-a-study-schedule-that-works"],
    },
    {
        "slug": "sat-study-schedule",
        "kicker": "Test prep",
        "title": "A 6-Week SAT Study Schedule That Fits a School Week",
        "meta_title": "6-Week SAT Study Schedule | IntelliPlan",
        "description": "A week-by-week SAT plan built around school, with practice-test timing, error review, and the score gains that come first.",
        "date": "2026-08-10",
        "reading_time": 6,
        "sections": [
            {"paragraphs": [
                "Six weeks is enough to move an SAT score meaningfully, and not enough to relearn high school maths. The plan below assumes about five hours a week, which is what a student with a full schedule can actually sustain, and it spends most of that time on the two things that move scores fastest: familiarity with the test and review of your own errors.",
                "Take one full practice test before you start. The score matters less than the breakdown by section and question type.",
            ]},
            {"h2": "Weeks one and two: find the leaks",
            "paragraphs": [
                "Do one timed section per study block, alternating between Reading and Writing and Maths. After each, spend as long reviewing as you spent doing it. Review is where the gain is: for every wrong answer, write the reason in one of four categories. Did not know the content, misread the question, ran out of time, careless error.",
                "By the end of week two you should have a ranked list of your three most expensive question types. That list replaces the generic study plan from here on.",
            ]},
            {"h2": "Weeks three and four: targeted practice",
            "paragraphs": [
                "Spend 70 percent of each block on your three expensive types and 30 percent on everything else. Use real practice questions and check every answer, including the ones you got right by guessing.",
                "For maths, keep a formula sheet you build yourself and use it on practice sets. For Reading, practise finding the line that answers the question before looking at the options. Most wrong answers are true statements that do not answer the question asked.",
            ]},
            {"h2": "Week five: full tests under real conditions",
            "paragraphs": [
                "Two full practice tests this week, at the same time of day as your real test, with the same breaks and no phone. The point is stamina and pacing, not the score.",
                "After each, do a full review pass and update your error list. If a section is consistently finishing with time left, practise slowing down; if it is consistently running out, practise skipping the hardest question and returning.",
            ]},
            {"h2": "Week six: light and sharp",
            "paragraphs": [
                "No new material. Redo the questions you got wrong in weeks one and two, review your formula sheet, and take one short timed section two days before the test. The day before, do nothing academic and sleep.",
            ]},
            {"h2": "Fitting it into a school week",
            "paragraphs": [
                "Five hours is two weekday blocks of 45 minutes plus one longer weekend block, and it has to coexist with homework. Put the SAT blocks in the calendar first, treat them as fixed commitments, and let the planner fit assignments around them. The reverse never works: there is always more homework.",
                "Use the small gaps for vocabulary and formula flashcards. They are the one part of test prep that genuinely fits into ten minutes on a bus.",
            ]},
            {"h2": "What not to do",
            "paragraphs": [
                "Do not take a practice test every day; you will run out of real material and learn the tests rather than the skills. Do not study only your strongest section because it feels good. And do not buy a new prep book in week five. The material you already have, reviewed properly, is worth more.",
            ]},
        ],
        "related": ["how-to-build-a-college-application-timeline", "spaced-repetition-for-students", "how-to-study-for-math-tests"],
    },
    {
        "slug": "how-to-study-in-a-distracting-dorm",
        "kicker": "Focus",
        "title": "How to Study in a Distracting Dorm or a Busy House",
        "meta_title": "How to Study in a Distracting Dorm | IntelliPlan",
        "description": "You cannot always get a quiet room. Practical ways to study in a dorm, a shared bedroom, or a loud house without losing the evening.",
        "date": "2026-08-06",
        "reading_time": 4,
        "sections": [
            {"paragraphs": [
                "Most study advice quietly assumes a door you can close. If you live in a dorm, share a bedroom, or study in a house where the TV is always on, the useful question is not how to find silence but how to work without it.",
                "Four things help, roughly in order of how much difference they make.",
            ]},
            {"h2": "Change the input, not the volume",
            "paragraphs": [
                "Unpredictable noise is worse than loud noise, because your attention keeps checking whether the next sound matters. A steady sound masks the unpredictable one. Brown noise, a fan, or an instrumental playlist at low volume will do more than noise-cancelling headphones playing nothing.",
                "Lyrics are the exception. Words compete with reading in a way that instruments do not, so a familiar vocal track is worse than a boring instrumental one.",
            ]},
            {"h2": "Make the desk the only option",
            "paragraphs": [
                "Studying on a bed in a shared room with a phone nearby is three distractions stacked. A small desk, a lamp that means work, and a chair that is not comfortable enough to nap in will do more for your focus than any app.",
            ], "list": [
                "Phone in a drawer or another room, not face-down on the desk.",
                "One tab open. If you need a reference, open it in a second window you close between problems.",
                "Headphones on even when the room is quiet. It signals to everyone else that you are working.",
            ]},
            {"h2": "Work in shorter blocks and take real breaks",
            "paragraphs": [
                "In a distracting environment, long blocks fail because one interruption ends them. Twenty-five minutes is short enough that an interruption costs one block instead of the evening. Take the break away from the desk, ideally outside the room.",
            ]},
            {"h2": "Leave when it matters",
            "paragraphs": [
                "Some work needs a room you cannot get at home: a practice test, an essay draft, a lab report. The library, a study hall, a cafe at a quiet hour, or a friend's empty common room are all legitimate. Doing the two hardest hours of the week somewhere quiet and the rest at home is a better plan than trying to make one environment do everything.",
            ]},
            {"h2": "Make the plan visible to the people you live with",
            "paragraphs": [
                "Most interruptions are not malicious; they are people not knowing you are working. A visible schedule, a closed door, or a simple agreement about the hour before dinner reduces them more than headphones do.",
            ]},
            {"h2": "Use blocking that ends",
            "paragraphs": [
                "A site blocker that runs all day gets turned off by Wednesday. One that runs only during a planned study block is easier to keep, because it has an end you can see. IntelliPlan's Focus Shield works that way in the browser extension: it blocks your distractor list only while a planned block is running, and it does not report your browsing anywhere.",
            ]},
        ],
        "related": ["pomodoro-technique-for-students", "how-to-study-with-adhd", "time-blocking-for-students"],
    },
    {
        "slug": "how-to-use-a-study-group-without-wasting-time",
        "kicker": "Study groups",
        "title": "How to Run a Study Group That Is Not Just Hanging Out",
        "meta_title": "How to Run a Study Group That Works | IntelliPlan",
        "description": "Most study groups become social hours. A simple format, the right size, and rules that keep the group useful.",
        "date": "2026-08-02",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "A study group is one of the few things that reliably improves understanding, and most of them turn into a conversation with textbooks open. The difference is not the people; it is whether the session has a format.",
                "The format below works for two to four people and about ninety minutes.",
            ]},
            {"h2": "Pick the right size and the right people",
            "paragraphs": [
                "Two to four is the useful range. A fifth person mostly adds coordination. The best group is not your closest friends. It is people taking the same course who will notice if you have not done the work.",
            ]},
            {"h2": "Set the goal before you arrive",
            "paragraphs": [
                "Each person brings one topic they can explain and one they cannot. The session is for the second list. \"Let's review for the test\" is not a goal; \"be able to explain how the Krebs cycle produces ATP without notes\" is.",
            ]},
            {"h2": "Teach first, discuss second",
            "paragraphs": [
                "Spend the first thirty minutes with each person teaching their chosen topic for five minutes without notes, while the others ask questions. Teaching exposes gaps that rereading hides, and it is uncomfortable in exactly the productive way.",
                "Then work problems together, but attempt them individually first. Group problem-solving where one person drives and everyone nods is a lecture with extra steps.",
            ]},
            {"h2": "Protect a silent block",
            "paragraphs": [
                "Twenty minutes at the start or middle with no talking, everyone working on their own hardest task, converts the group from a social event into a work session. It sounds awkward and it is the part people ask for again.",
            ]},
            {"h2": "End with a shared list",
            "paragraphs": [
                "In the last five minutes, each person says what they still cannot do and what they will do about it before the next session. Write it down. The group's memory is better than yours, and the next session starts from the real gaps rather than from the beginning.",
            ]},
            {"h2": "Keep the logistics out of the chat",
            "paragraphs": [
                "A group chat is a poor place for the plan and a good place for the link. Put the session time, the goal and the shared task list somewhere everyone can see. IntelliPlan's groups support shared tasks and a live study room, which is useful mainly because it keeps the session's purpose next to the work instead of buried in messages.",
                "If your group meets remotely, a live room with a shared timer works better than a video call with no structure: everyone joins, states a goal, works silently, and reports back at the end.",
            ]},
            {"h2": "Know when to leave",
            "paragraphs": [
                "A group that has become a weekly social hour is fine as a social hour. It is not a study group, and pretending otherwise costs you the evening. Study with two people who work, and see your friends after.",
            ]},
        ],
        "related": ["active-recall-vs-rereading", "how-to-study-in-a-distracting-dorm", "how-to-make-a-study-schedule-that-works"],
    },
    {
        "slug": "how-parents-can-help-without-taking-over",
        "kicker": "Families",
        "title": "How Parents Can Help With Schoolwork Without Taking Over",
        "meta_title": "How Parents Can Help Without Taking Over | IntelliPlan",
        "description": "Support that builds independence instead of dependence: what to ask, what to stop checking, and how to help when a student is behind.",
        "date": "2026-07-29",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Most parents who want to help end up doing one of two things: checking the portal every night, or backing off entirely because checking turned into arguing. Both are responses to the same problem. There is no shared picture of the week that is not an interrogation.",
                "The middle path is to help with the system rather than with the work.",
            ]},
            {"h2": "Ask about the plan, not the grades",
            "paragraphs": [
                "\"What is due this week?\" is a better question than \"did you check your grades?\" because it is answerable, actionable, and it puts the student in charge of the answer. If they cannot answer it, that is the thing to fix, and it is fixable in ten minutes with a calendar.",
            ]},
            {"h2": "Help build the system once",
            "paragraphs": [
                "Sit down together once and set up one place where assignments live, one weekly time to review it, and one agreement about what happens when something is missed. Then stop managing it. A system the student owns survives; a system the parent runs collapses the moment the parent stops.",
            ], "list": [
                "One list, visible to the student, not a spreadsheet the parent maintains.",
                "A fixed ten-minute weekly review, on a day that is not already stressful.",
                "An agreement about the signal: what the student says when they are behind, before it is a crisis.",
            ]},
            {"h2": "Stop refreshing the portal",
            "paragraphs": [
                "Checking grades daily creates anxiety in the student and a sense of surveillance that makes honest conversations harder. Weekly is enough for a parent, and it is enough to catch a problem while it is still small.",
                "If you use IntelliPlan's family view, the useful part is the shared picture of what is coming, not a live grade feed. The point is to reduce the number of conversations that start with a number.",
            ]},
            {"h2": "When the student is behind",
            "paragraphs": [
                "The instinct is to add structure and pressure at the same moment. That usually produces compliance and no learning. A better sequence: find out what is actually missing, pick the highest-value items, and help with the logistics: a quiet hour, a ride to the library, an email to the teacher written together.",
                "The single most useful thing a parent can do in a bad week is remove a constraint, not add a rule.",
            ]},
            {"h2": "Respect the age curve",
            "paragraphs": [
                "A seventh grader needs help building the habit. A senior needs the habit to be theirs, and help that looks like supervision will be rejected even when it is wanted. The same support looks different at 12 and 17, and asking which one they want is not abdication.",
            ]},
            {"h2": "What to do about phones",
            "paragraphs": [
                "Rules that apply to the whole household work better than rules that single out one person, and rules enforced only during study time work better than rules that last all day. The goal is a study environment, not a punishment.",
            ]},
        ],
        "related": ["how-to-make-a-study-schedule-that-works", "how-to-catch-up-when-behind-in-school", "how-to-stop-procrastinating-on-homework"],
    },
    {
        "slug": "how-to-teach-yourself-a-subject",
        "kicker": "Self-study",
        "title": "How to Teach Yourself a Subject Without a Teacher",
        "meta_title": "How to Teach Yourself a Subject | IntelliPlan",
        "description": "A self-study method for a course you are failing, a subject your school does not offer, or a gap you need to close before next year.",
        "date": "2026-07-25",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "Teaching yourself something is mostly a sequencing problem. The material is usually available (a textbook, a course, a set of videos) and the failure is starting in the middle, working without feedback, or never testing whether it went in.",
                "This method works for a subject you need to catch up on and for one you are learning for its own sake.",
            ]},
            {"h2": "Find the spine first",
            "paragraphs": [
                "Before you start, write the ten to fifteen things the subject is actually about. Use a table of contents, a syllabus, or a course outline. This map is what stops you from spending three weeks on the first chapter and never reaching the exam.",
                "Then mark each item as known, heard of, or new. Most self-study plans fail because they start at item one regardless.",
            ]},
            {"h2": "Work in small loops, not long marathons",
            "paragraphs": [
                "A useful loop is: read or watch a short segment, close it, write what you understood, do two or three problems, check. Each loop should be under an hour. Long sessions without checking produce confidence and not much else.",
                "If you cannot do the problems, the segment was too big or too advanced. Go back one step rather than pushing forward.",
            ]},
            {"h2": "Get feedback from something other than yourself",
            "paragraphs": [
                "The hardest part of self-study is not knowing whether you are right. Use anything external: an answer key, worked solutions, a past paper with a mark scheme, an online problem set that checks answers, or a person who knows the subject.",
                "For writing and explanations, a language model can act as a first reader. Ask it to point out where your explanation is vague or wrong, not to write the explanation. The value is in the critique, and you have to verify the critique against your source material.",
            ]},
            {"h2": "Build retrieval into the schedule",
            "paragraphs": [
                "Once a week, close everything and write what you know about the last three topics, then check. This is the same active recall that works in school, and it matters more when nobody is testing you, because otherwise the only feedback you get is the feeling of having read something.",
            ]},
            {"h2": "Use a real deadline",
            "paragraphs": [
                "Self-study with no deadline expands to fill the time available, which is to say it never finishes. Pick a date and a proof: a practice exam, a project, an essay, a conversation with someone who knows the subject. Then back-plan from it the way you would for a class.",
            ]},
            {"h2": "Where AI helps and where it hurts",
            "paragraphs": [
                "A tutor that explains a step you are stuck on, and then asks you to do the next one, is useful. A tool that produces the answer is not, even when it is faster. The test is simple: are you doing the thinking, or watching it being done?",
                "IntelliPlan's Plani tutor is built around that distinction: it teaches step by step and checks understanding with follow-up questions rather than handing over solutions. It is not a replacement for a teacher, and for a subject you are teaching yourself it is most useful as the thing that asks you the question you would have skipped.",
            ]},
        ],
        "related": ["active-recall-vs-rereading", "how-to-prepare-for-ap-exams", "how-to-study-with-adhd"],
    },
    {
        "slug": "how-to-connect-powerschool-to-a-study-planner",
        "kicker": "PowerSchool",
        "title": "How to Get PowerSchool Assignments Into a Study Planner",
        "meta_title": "PowerSchool Assignments in a Planner | IntelliPlan",
        "description": "District portals rarely offer student APIs. How PowerSchool, Aeries and similar portals get into a planner, and what to check.",
        "date": "2026-07-21",
        "reading_time": 5,
        "sections": [
            {"paragraphs": [
                "PowerSchool, Aeries, Infinite Campus, Skyward and eSchoolPlus have something in common: they are run by the district, and they do not issue API keys to students. There is no developer portal to sign up for and no OAuth flow to approve. That is why most planners support Canvas and Google Classroom and quietly do not support the portal your school actually uses.",
                "The workaround is not an API. It is reading the page you are already signed in to, in your own browser, and importing what it shows.",
            ]},
            {"h2": "How the import actually works",
            "paragraphs": [
                "IntelliPlan's Chrome extension has scrapers for the major district portals, including PowerSchool, Aeries, Infinite Campus, Skyward and eSchoolPlus. When you are on your assignments page, the extension reads the page content and sends the assignment list to your IntelliPlan account using your own login token.",
                "Two things follow from that design, and both matter. Your district password is never sent to IntelliPlan. The extension uses the session your browser already has. And the import is a copy: it does not change anything in the portal, and if a teacher edits an assignment you re-sync to pick it up.",
            ]},
            {"h2": "The steps",
            "paragraphs": ["It takes a few minutes the first time and about ten seconds after that."], "list": [
                "Install the IntelliPlan extension and sign in with your IntelliPlan account.",
                "Open your district portal and go to the page that lists assignments or grades.",
                "Click the extension and choose sync. It detects the portal by the page you are on.",
                "Check the dashboard: the assignments should be on the same list as everything else.",
            ]},
            {"h2": "What to check after the first sync",
            "paragraphs": [
                "District portals vary by version and by district, so a scraper is more fragile than an API. Three checks catch most problems.",
            ], "list": [
                "Count: does the number of imported assignments match what the page shows?",
                "Dates: portals sometimes show a due date and a separate due time, or use a different date format. Check one you know.",
                "Categories: if the portal groups by category, make sure the import did not treat a category header as an assignment.",
            ]},
            {"h2": "Why not just use the portal",
            "paragraphs": [
                "A portal shows you one class at a time and ranks nothing. The value of importing is that every class lands on one list, ranked by due date, points and course weight, so a quiz tomorrow in a class worth 20 percent of your grade outranks a worksheet next week in one worth 5.",
                "It also means the plan can be built from the real list rather than from what you remembered to write down.",
            ]},
            {"h2": "If your portal is not supported",
            "paragraphs": [
                "The manual fallback is the same as it has always been: add the assignments yourself, once a week, in the same place. Slower, and still better than a second list you forget to check. Infinite Campus and Skyward are the most requested additions; the provider list in IntelliPlan's settings shows what is currently supported.",
            ]},
        ],
        "related": ["how-to-use-canvas-with-a-study-planner", "how-to-use-google-classroom-with-a-planner", "how-to-prioritize-assignments"],
    },
]

BY_SLUG: dict[str, dict] = {article["slug"]: article for article in ARTICLES}

#: The nine guides that predate this registry. They are still standalone
#: templates with their own routes, but they are real articles and the newer
#: ones link to them, so related links need to resolve against them too.
LEGACY_TITLES: dict[str, str] = {
    "best-ai-study-planner": "The Best AI Study Planner for Students",
    "best-student-planner-app": "The Best Student Planner App",
    "ai-notetaker-for-students": "The Best AI Notetaker for Students",
    "how-to-use-canvas-with-a-study-planner": "How to Use Canvas with a Study Planner",
    "studentvue-study-planner": "Using StudentVue with a Study Planner",
    "how-to-prioritize-assignments": "How to Prioritize Assignments",
    "what-is-a-good-gpa": "What Is a Good GPA?",
    "ap-study-planner": "AP Study Planner",
}


def get(slug: str) -> dict | None:
    return BY_SLUG.get(slug)


def resolve(slug: str) -> dict | None:
    """A ``{slug, title}`` reference for any published guide, old or new.

    Related links render only the slug and the title, so a registry article
    and a legacy template are interchangeable here.
    """
    article = BY_SLUG.get(slug)
    if article:
        return {"slug": article["slug"], "title": article["title"]}
    title = LEGACY_TITLES.get(slug)
    return {"slug": slug, "title": title} if title else None


def all_articles() -> list[dict]:
    """Newest first, which is the order the index and sitemap render."""
    return sorted(ARTICLES, key=lambda article: article["date"], reverse=True)


def article_path(article: dict) -> str:
    return article.get("path") or f"/blog/{article['slug']}"
