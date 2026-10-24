"""Discover existing study tools without changing their authorization rules."""
from intelliplan.learning import policy


# Title, destination, description, icon, required capabilities.
GROUPS = (
    ('understand', 'Understand something', 'Start with a question and work through your reasoning.', (
        ('Plani tutor', '/study-and-learn', 'Chat, explore interactive examples, and get a hint when you need one.', 'robot', ('tutor',)),
        ('Writing coach', '/writing', 'Get feedback on your draft and develop your own argument.', 'pencil', ('tutor',)),
        ('Math explainer', '/math', 'Explore the reasoning behind a mathematical idea.', 'modeler', ('tutor',)),
        ('Text dissector', '/tools/text-dissector', 'Break down a reading and find questions to think about.', 'book', ('tutor',)),
        ('Task extractor', '/extractor', 'Turn an assignment description into tasks you can plan.', 'check-circle', ('tutor',)),
    )),
    ('practice', 'Practice & remember', 'Retrieve an idea, try an example, and check your understanding.', (
        ('Flashcards', '/flashcards', 'Return to your saved decks and review what is due.', 'clipboard', ('review',)),
        ('Photo practice', '/flashcards/photo', 'Turn a photo of your notes into grounded quizzes and flashcards.', 'study', ('review', 'study', 'uploads', 'tutor')),
        ('Quizzes & recall', '/learn', 'Practice from your study material and revisit weak areas.', 'study', ('review',)),
        ('Foundations', '/foundations', 'Work through reviewed concept checks at your own pace.', 'book', ('study',)),
        ('Competition practice', '/olympiad', 'Explore subject challenges and competition preparation.', 'fire', ('study',)),
        ('Lessons', '/lessons', 'Revisit your saved lessons or create one from your material.', 'lightbulb', ('study', 'uploads')),
    )),
    ('study', 'Settle into studying', 'Bring your materials and choose how you want to work.', (
        ('Deep study', '/study', 'Turn your notes into a structured study activity.', 'study', ('study',)),
        ('Active study', '/active', 'Work through a scheduled block with a timer and checkpoints.', 'clock', ('study',)),
        ('Focus tools', '/focus', 'Set up a focus session and your optional distraction blocker.', 'clock', ('focus',)),
        ('Study library', '/library', 'Find and return to your saved learning resources.', 'backpack', ('study',)),
        ('Study materials', '/study-files', 'Open the study files you have saved.', 'save', ('study',)),
        ('Sessions & progress', '/learning', 'Set a study goal, record your work, and review your progress.', 'grades', ('study',)),
    )),
    ('plan', 'Plan your schoolwork', 'Keep track of what is coming and make room for it.', (
        ('Scheduler', '/scheduler', 'Build a plan around your deadlines and available time.', 'scheduler', ('scheduler',)),
        ('Priority list', '/priority', 'See which assignments need your attention next.', 'priority', ()),
        ('Classes', '/classes', 'Return to your courses and class details.', 'classes', ()),
        ('Grades', '/grades', 'See your connected grade information.', 'grades', ()),
        ('Tests', '/tests', 'Keep upcoming tests and preparation in view.', 'test', ()),
        ('Dismissed assignments', '/dismissed', 'Review assignments you have set aside.', 'done', ()),
    )),
    ('together', 'Learn together', 'Connect through the existing group and meeting tools.', (
        ('Study groups', '/groups', 'Return to your study groups and shared sessions.', 'users', ()),
        ('Meetings', '/meetings', 'Open your meeting space for a study session.', 'video', ()),
    )),
)


def groups_for(user):
    decisions = {key: policy.decision(user, key) for key in policy.FEATURES}
    result = []
    for key, title, description, entries in GROUPS:
        tools = []
        for name, href, summary, icon, features in entries:
            denied = next((decisions[f][1] for f in features if not decisions[f][0]), None)
            tools.append(dict(name=name, href=href, description=summary, icon=icon,
                              allowed=denied is None, reason=denied))
        result.append(dict(key=key, title=title, description=description, tools=tools))
    return result
