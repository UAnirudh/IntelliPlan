"""Reviewed link metadata only: no copied lessons, ratings, or rehosted videos.

Links were checked 2026-10-09. License URLs describe the source's terms;
linking here grants no redistribution rights. Unknown topics fail honestly.
"""
RESOURCES = [
    dict(id='python-start', title='Python: an informal introduction',
         url='https://docs.python.org/3/tutorial/introduction.html',
         provider='Python Software Foundation', license_url='https://docs.python.org/3/license.html',
         topics=['variables', 'arithmetic', 'strings'], level='beginner', video=False),
    dict(id='python-flow', title='Python: control flow',
         url='https://docs.python.org/3/tutorial/controlflow.html',
         provider='Python Software Foundation', license_url='https://docs.python.org/3/license.html',
         topics=['conditions', 'loops', 'functions'], level='beginner', video=False),
    dict(id='cs50-functions', title='CS50 Python: functions and variables',
         url='https://cs50.harvard.edu/python/weeks/0/', provider='Harvard CS50',
         license_url='https://cs50.harvard.edu/python/license/',
         topics=['variables', 'strings', 'functions'], level='beginner', video=True),
    dict(id='cs50-loops', title='CS50 Python: loops',
         url='https://cs50.harvard.edu/python/weeks/2/', provider='Harvard CS50',
         license_url='https://cs50.harvard.edu/python/license/',
         topics=['loops', 'conditions'], level='beginner', video=True),
    dict(id='python-structures', title='Python: data structures',
         url='https://docs.python.org/3/tutorial/datastructures.html',
         provider='Python Software Foundation', license_url='https://docs.python.org/3/license.html',
         topics=['lists', 'dictionaries'], level='beginner', video=False),
    dict(id='cs50-testing', title='CS50 Python: unit tests',
         url='https://cs50.harvard.edu/python/weeks/5/', provider='Harvard CS50',
         license_url='https://cs50.harvard.edu/python/license/',
         topics=['testing', 'functions'], level='beginner', video=True),
    dict(id='mit-matrices', title='MIT 18.06: linear algebra',
         url='https://ocw.mit.edu/courses/18-06-linear-algebra-spring-2010/',
         provider='MIT OpenCourseWare', license_url='https://ocw.mit.edu/pages/privacy-and-terms-of-use/',
         topics=['matrices', 'linear algebra', 'vectors'], level='intermediate', video=True),
]


def rank(objective):
    words = objective.lower()
    ranked = sorted(RESOURCES, key=lambda r: sum(t in words for t in r['topics']), reverse=True)
    matches = [dict(r, access='link_only', checked_on='2026-10-09') for r in ranked
               if any(t in words for t in r['topics'])]
    return matches[:3]


def python_blueprint(goal):
    topics = ['variables and strings', 'conditions', 'loops', 'functions', 'lists and dictionaries', 'testing']
    return dict(title='Python foundations', overview=goal, prerequisites=[],
                outcomes=['Write and test small Python programs'],
                lessons=[dict(key=f'python-{i}', title=topic.title(), objective=topic,
                              prerequisites=[f'python-{i-1}'] if i else [], minutes=30,
                              unit='Foundations' if i < 3 else 'Building programs')
                         for i, topic in enumerate(topics)])
