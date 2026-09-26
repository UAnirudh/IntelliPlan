"""Reviewed, branching story content for the Foundations learning journey.

Choices change later scenes, but never change a learner's assessed skill or
reward a particular moral answer. The learning task remains independently
reviewable in catalog.py.
"""

from dataclasses import dataclass


BEATS = (
    ('Reading', 'Read the clue', 'Find meaning in the next clue.'),
    ('Writing', 'Put it into words', 'Help the story move forward with a sentence.'),
    ('Arithmetic', 'Work out what is needed', 'Use numbers to make a plan.'),
)


@dataclass(frozen=True)
class Choice:
    id: str
    label: str
    consequence: str


@dataclass(frozen=True)
class Chapter:
    title: str
    scene: str
    question: str
    choices: tuple[Choice, Choice]
    conversation: str


STORIES = {
    'forest': (
        Chapter('The missing trail',
                'A small lantern glows beside a path that has disappeared under fallen leaves. A fox waits where the trail divides.',
                'How should we begin?',
                (Choice('ask', 'Ask the fox what it saw', 'The fox remembers a quiet stream beyond the trees.'),
                 Choice('look', 'Look for marks on the ground', 'You find a line of tiny footprints beside the leaves.')),
                'What could we learn by asking someone? What could we learn by looking closely?'),
        Chapter('The stream crossing',
                'The path reaches a stream. Across the water, someone has left a folded note tied to a branch.',
                'How could everyone cross safely?',
                (Choice('bridge', 'Build a little bridge together', 'The animals carry branches and make a crossing for everyone.'),
                 Choice('stones', 'Find the stepping stones', 'You mark a careful path across the stones for the next traveler.')),
                'Who else might need to use the crossing after us?'),
        Chapter('The garden gate',
                'Beyond the stream is a garden with its gate stuck shut. A gardener waves from inside.',
                'What should we do before opening the gate?',
                (Choice('listen', 'Listen to the gardener', 'The gardener explains which latch is safe to lift.'),
                 Choice('help', 'Gather friends to help', 'Your friends hold the gate steady while you lift the latch.')),
                'When is it useful to pause and ask for help?'),
        Chapter('A path for tomorrow',
                'The garden is open. New travelers will need a way to find it tomorrow, even when the leaves fall again.',
                'What will we leave for the next traveler?',
                (Choice('map', 'Draw a map of the path', 'A clear map helps new travelers find their way.'),
                 Choice('sign', 'Make a sign at each turn', 'Simple signs make the path easier to follow.')),
                'How would you explain the path to someone who has never seen it?'),
    ),
    'space': (
        Chapter('The quiet signal',
                'A gentle signal reaches the station from a tiny planet. It repeats three times, then goes quiet.',
                'What should we do first?',
                (Choice('listen', 'Listen to the signal again', 'You notice a small rhythm hidden inside the signal.'),
                 Choice('search', 'Search the star chart', 'You find a tiny planet marked near the edge of the chart.')),
                'What helps you understand a message you have not heard before?'),
        Chapter('The drifting lantern',
                'On the way to the planet, a lantern drifts away from a nearby ship. Someone may need it to find home.',
                'How can we help?',
                (Choice('retrieve', 'Bring the lantern back', 'The crew can see its way home again.'),
                 Choice('guide', 'Send directions to the crew', 'The crew follows your directions to the lantern.')),
                'How can two different plans solve the same problem?'),
        Chapter('The planet library',
                'The signal leads to a little library. Its door opens only when visitors share what they have learned.',
                'What should we share?',
                (Choice('clue', 'Tell the story of our clue', 'The library saves your clue for the next visitor.'),
                 Choice('plan', 'Show the plan we made', 'The library saves your plan for the next visitor.')),
                'What would you want the next visitor to know?'),
        Chapter('A message home',
                'The library helps you send a message home. You can include one thing that will help another explorer.',
                'What belongs in the message?',
                (Choice('question', 'Share a question to investigate', 'Your question gives the next explorer a place to begin.'),
                 Choice('discovery', 'Share a discovery from the journey', 'Your discovery helps the next explorer notice more.')),
                'What did you change your mind about during this journey?'),
    ),
    'ocean': (
        Chapter('The fading reef',
                'A reef near the ocean garden has lost some of its color. A little turtle waits beside it.',
                'How should we begin?',
                (Choice('ask', 'Ask the turtle what changed', 'The turtle remembers a new current near the reef.'),
                 Choice('observe', 'Look carefully at the water', 'You see that the water is moving in a new direction.')),
                'What is the difference between a guess and something we have observed?'),
        Chapter('The new current',
                'The current carries tiny pieces of the garden away. Other sea creatures are trying to protect their homes.',
                'What could we try together?',
                (Choice('shelter', 'Build a shelter for the garden', 'The shelter gives the tiny plants a calm place to grow.'),
                 Choice('route', 'Find a calmer route for the water', 'The water follows a gentler route around the garden.')),
                'How could we tell whether our plan is helping?'),
        Chapter('The visiting whale',
                'A whale arrives and asks why the garden looks different. It knows the ocean beyond the reef.',
                'What should we do?',
                (Choice('explain', 'Explain what we observed', 'The whale understands the change and shares what it has seen.'),
                 Choice('invite', 'Invite the whale to look with us', 'Together you notice a place that still needs care.')),
                'Why might another point of view change a plan?'),
        Chapter('A garden for everyone',
                'The reef is changing slowly. The garden needs a plan that another visitor can follow tomorrow.',
                'What will we leave behind?',
                (Choice('guide', 'Make a simple care guide', 'The next visitor has steps they can follow.'),
                 Choice('markers', 'Mark the places to watch', 'The next visitor knows where to look first.')),
                'What would you check next week to see if the garden is improving?'),
    ),
}
CHAPTER_COUNT = 4

BEAT_CUES = {
    'forest': (
        ('The fox has a word clue at the fork in the trail.', 'Practice making a clear message for the next traveler.', 'Count carefully before choosing what to carry.'),
        ('A note hangs on the far side of the stream.', 'A short sentence can help explain the crossing.', 'Work out how many things the crossing needs.'),
        ('The gardener has left a clue beside the gate.', 'Put a helpful instruction into words.', 'Count what is needed to open the way.'),
        ('Read one more clue before marking the path.', 'Make a message that another traveler could follow.', 'Use numbers to check the plan for tomorrow.'),
    ),
    'space': (
        ('Listen for a word clue inside the signal.', 'Send a clear sentence back to the station.', 'Count what the first plan needs.'),
        ('A message from the nearby ship gives a clue.', 'Write a short message the crew can understand.', 'Use numbers to plan the next move.'),
        ('The library shares a reading clue.', 'Put one discovery into a sentence.', 'Count what belongs in the library record.'),
        ('Read the last clue before sending the message.', 'Practice writing a sentence for another explorer.', 'Use numbers to double-check the message.'),
    ),
    'ocean': (
        ('Look for a word clue near the little turtle.', 'Practice describing what you noticed.', 'Count the pieces of the garden carefully.'),
        ('A clue floats along the new current.', 'Put the care plan into a short sentence.', 'Use numbers to plan the work.'),
        ('Read a clue from the visiting whale.', 'Practice explaining what changed.', 'Count what the garden needs next.'),
        ('Read the last clue for tomorrow’s visitor.', 'Write a sentence that can guide someone else.', 'Use numbers to check the care plan.'),
    ),
}


def view(world: str, chapter_index: int, beat: int, path: list[str], repair: bool = False) -> dict:
    chapters = STORIES[world]
    history = []
    for index, choice_id in enumerate(path[:len(chapters)]):
        choice = next((entry for entry in chapters[index].choices if entry.id == choice_id), None)
        if choice:
            history.append({'chapter': chapters[index].title, 'choice': choice.label,
                            'consequence': choice.consequence})
    if chapter_index >= len(chapters):
        last_choice = next((choice.consequence for choice in chapters[-1].choices
                            if path and choice.id == path[-1]), None)
        return {
            'complete': True, 'chapter': len(chapters), 'chapter_count': len(chapters),
            'title': 'Your story continues',
            'scene': 'You have finished this journey. Start a new one to revisit the world with fresh choices and more practice.',
            'previous_choice': last_choice,
            'history': history,
        }
    chapter = chapters[chapter_index]
    previous = None
    if chapter_index and len(path) >= chapter_index:
        prior = chapters[chapter_index - 1]
        previous = next((choice.consequence for choice in prior.choices if choice.id == path[chapter_index - 1]), None)
    result = {
        'complete': False, 'chapter': chapter_index + 1, 'chapter_count': len(chapters),
        'beat': beat + 1 if beat < len(BEATS) else len(BEATS),
        'beat_count': len(BEATS), 'title': chapter.title, 'scene': chapter.scene,
        'previous_choice': previous, 'conversation': chapter.conversation,
        'history': history,
        'awaiting_choice': beat >= len(BEATS),
    }
    if beat < len(BEATS):
        result['repair'] = repair
        result['beat_title'] = 'Try another clue' if repair else BEATS[beat][1]
        result['beat_intro'] = ('The last clue was tricky. Try a different example of the same skill.'
                                if repair else BEAT_CUES[world][chapter_index][beat])
    else:
        result['question'] = chapter.question
        result['choices'] = [{'id': choice.id, 'label': choice.label} for choice in chapter.choices]
    return result


def valid_choice(world: str, chapter_index: int, choice_id: str) -> bool:
    return 0 <= chapter_index < len(STORIES[world]) and any(
        choice.id == choice_id for choice in STORIES[world][chapter_index].choices
    )
