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

# The same choice IDs keep journey history stable if an adult changes grade.
ADVANCED_STORIES = {
    'forest': (
        Chapter('The missing trail',
                'A once-used trail is hidden beneath fallen leaves. At a fork, a fox has seen travelers turn back. The group needs evidence before choosing a route.',
                'Which source should guide the first step?',
                (Choice('ask', 'Interview the fox', 'The fox recalls a stream beyond the eastern trees.'),
                 Choice('look', 'Inspect the ground', 'Footprints continue beneath the leaves toward a stream.')),
                'What might each source know, and how could the group check it?'),
        Chapter('The stream crossing',
                'The route reaches a stream used by more than one traveler. A note on the far bank suggests a crossing, but its author is unknown.',
                'Which crossing plan can the group defend?',
                (Choice('bridge', 'Build a shared bridge', 'The group builds a crossing that others can use afterward.'),
                 Choice('stones', 'Mark the stable stones', 'A marked route helps later travelers cross with care.')),
                'Who might be affected by the crossing after the group leaves?'),
        Chapter('The garden gate',
                'At the end of the trail, a gate is jammed. The gardener can see the latch from the other side, while several travelers offer to help.',
                'How should the group handle the gate?',
                (Choice('listen', 'Ask the gardener about the latch', 'The gardener identifies the safe release before anyone moves the gate.'),
                 Choice('help', 'Coordinate the helpers', 'The helpers steady the gate while one person releases the latch.')),
                'Which information would make the plan safer?'),
        Chapter('A path for tomorrow',
                'The route is open again. New travelers will need a way to navigate it even after the landmarks change.',
                'Which guide should the group leave behind?',
                (Choice('map', 'Publish a route map', 'A map records the route and its important landmarks.'),
                 Choice('sign', 'Place signs at decisions', 'Signs help travelers decide at each fork.')),
                'How would the group test whether its guide works for a first-time traveler?'),
    ),
    'space': (
        Chapter('The quiet signal',
                'A faint transmission reaches the station from an unlisted planet. It repeats in a pattern, then stops. The crew has limited time to decide what to investigate.',
                'Which lead should the crew examine first?',
                (Choice('listen', 'Analyze the signal pattern', 'A repeated interval gives the crew a clue about its source.'),
                 Choice('search', 'Check the star chart', 'An overlooked planet appears near the chart boundary.')),
                'What evidence would distinguish a message from random noise?'),
        Chapter('The drifting lantern',
                'A navigation beacon has drifted from another ship. The crew can recover it directly or help the ship locate it.',
                'Which response should the crew try?',
                (Choice('retrieve', 'Recover the beacon', 'The beacon returns to the ship before its next departure.'),
                 Choice('guide', 'Transmit its coordinates', 'The other crew uses the coordinates to retrieve the beacon.')),
                'How could the crew compare the risks of the two plans?'),
        Chapter('The planet library',
                'The signal leads to an archive maintained for future explorers. The archive asks visitors to contribute something verifiable.',
                'What should the crew contribute?',
                (Choice('clue', 'Document the signal evidence', 'The archive keeps the evidence with its source.'),
                 Choice('plan', 'Document the response plan', 'The archive keeps the plan and the reasoning behind it.')),
                'What would make this record useful to someone who was not here?'),
        Chapter('A message home',
                'Before leaving, the crew can send one short report to the station. It should help the next team make a better decision.',
                'Which report is most useful?',
                (Choice('question', 'Send a testable question', 'The next team begins with a clear investigation.'),
                 Choice('discovery', 'Send a supported finding', 'The next team can build on the recorded evidence.')),
                'How can a report separate an observation from an inference?'),
    ),
    'ocean': (
        Chapter('The fading reef',
                'Survey notes show a reef losing color. A turtle has noticed changes in the current, but the team needs more than one observation.',
                'Which evidence should the team gather first?',
                (Choice('ask', 'Interview the turtle', 'The turtle describes when the current first changed.'),
                 Choice('observe', 'Measure the water flow', 'The team records a new direction in the current.')),
                'How could the team test whether the current caused the change?'),
        Chapter('The new current',
                'The current carries pieces of the garden away. Nearby creatures propose different ways to protect it.',
                'Which trial should the team run?',
                (Choice('shelter', 'Test a sheltered area', 'The plants have a calmer place to grow.'),
                 Choice('route', 'Test a gentler water route', 'The current shifts away from the most fragile plants.')),
                'What measurement would show whether the trial helped?'),
        Chapter('The visiting whale',
                'A whale arrives from beyond the reef with observations from a wider area. The team can compare those observations with its own.',
                'How should the team use the new perspective?',
                (Choice('explain', 'Share the local observations', 'The whale adds a comparison from another reef.'),
                 Choice('invite', 'Survey together', 'The joint survey finds an area that still needs attention.')),
                'When can an outside observation strengthen or challenge a hypothesis?'),
        Chapter('A garden for everyone',
                'The reef is changing slowly. The next team will need a practical way to continue monitoring it.',
                'What should the team leave behind?',
                (Choice('guide', 'Write a monitoring guide', 'The next team has repeatable steps.'),
                 Choice('markers', 'Mark the survey locations', 'The next team can compare the same places over time.')),
                'What should the team record now to make a later comparison fair?'),
    ),
}

# Older learners use the same four decision points and stable choice IDs, but
# work through evidence, tradeoffs, and communication in realistic projects.
SCHOLAR_STORIES = {
    'forest': (
        Chapter('The accessible route', 'A community group is reopening a public trail. Its old map omits erosion and accessibility barriers, and several neighbors disagree about the safest route.',
                'What evidence should the team gather first?',
                (Choice('ask', 'Interview frequent trail users', 'Residents identify barriers that the old map misses.'),
                 Choice('look', 'Survey the route directly', 'The team records slope, erosion, and blocked crossings.')),
                'Whose experience might be missing from the available evidence?'),
        Chapter('A crossing with consequences', 'The shortest route crosses a stream. A bridge costs more now; marking a stone crossing requires less material but may exclude some visitors.',
                'Which plan should the team test?',
                (Choice('bridge', 'Model an accessible bridge', 'The proposal includes cost, maintenance, and access for different visitors.'),
                 Choice('stones', 'Assess the existing stone crossing', 'The team measures safety and access before recommending it.')),
                'How should immediate cost be weighed against long-term access?'),
        Chapter('The gate decision', 'The trail ends at a shared garden. Its gate is difficult to open, and the team needs permission before changing it.',
                'How should the team move forward?',
                (Choice('listen', 'Consult the garden steward', 'The steward explains ownership and a safe repair process.'),
                 Choice('help', 'Coordinate a volunteer assessment', 'Volunteers document the problem and propose a repair for approval.')),
                'What can the group do responsibly before it has permission?'),
        Chapter('Publish the route', 'The group must leave a guide others can use and revise when conditions change.',
                'What should the first public guide contain?',
                (Choice('map', 'Publish a sourced route map', 'The map notes survey dates, access limits, and uncertain sections.'),
                 Choice('sign', 'Place decision-point signs', 'Signs identify routes and provide a way to report changes.')),
                'How could a new visitor test whether the guide is genuinely usable?'),
    ),
    'space': (
        Chapter('An uncertain signal', 'A research team receives a faint repeating signal. A false alarm could waste scarce observation time; ignoring it could miss a discovery.',
                'Which first check is most useful?',
                (Choice('listen', 'Analyze the raw signal', 'The team records its timing and background noise.'),
                 Choice('search', 'Compare independent observations', 'A second instrument tests whether the pattern repeats.')),
                'What observation would change your confidence?'),
        Chapter('The missing beacon', 'A nearby craft reports a displaced navigation beacon. The research team can help, but a detour would delay its own mission.',
                'Which response can the team justify?',
                (Choice('retrieve', 'Recover the beacon', 'The team estimates the detour and safety risks first.'),
                 Choice('guide', 'Share verified coordinates', 'The other craft can recover it using a checked location.')),
                'What information is needed before judging either plan safe?'),
        Chapter('A record others can inspect', 'An archive asks for a reproducible account of the signal investigation, including uncertainty and decisions.',
                'What should the team deposit?',
                (Choice('clue', 'Publish the signal evidence', 'Raw observations and methods remain available for review.'),
                 Choice('plan', 'Publish the decision log', 'The log separates evidence from the team\'s interpretations.')),
                'How might another team challenge the conclusion?'),
        Chapter('The next expedition', 'A later crew will continue the work. The report must make clear what is known and what remains a hypothesis.',
                'Which message helps them most?',
                (Choice('question', 'State a testable next question', 'The crew can design an independent check.'),
                 Choice('discovery', 'Report a bounded finding', 'The finding includes source, uncertainty, and scope.')),
                'Where should a report draw the line between result and inference?'),
    ),
    'ocean': (
        Chapter('The reef survey', 'Local surveys suggest a reef is changing. Sampling sites were chosen for convenience, so the team cannot yet claim the whole coastline is affected.',
                'What should the team do first?',
                (Choice('ask', 'Interview local observers', 'Their accounts identify when and where conditions changed.'),
                 Choice('observe', 'Design a broader survey', 'The team selects comparison sites and repeatable measurements.')),
                'How could the sampling method distort the conclusion?'),
        Chapter('Test a response', 'Two restoration proposals compete for limited funds. The team needs a comparison that can reveal both benefits and unintended effects.',
                'Which pilot should begin?',
                (Choice('shelter', 'Test protected plots', 'The pilot measures growth against comparable unprotected plots.'),
                 Choice('route', 'Test a flow change', 'The pilot monitors water movement and effects downstream.')),
                'What result would count as evidence against the chosen plan?'),
        Chapter('A wider perspective', 'A regional researcher brings observations from other reefs. The methods differ from the local survey.',
                'How should the team use the new data?',
                (Choice('explain', 'Compare methods and findings', 'The team records differences before combining claims.'),
                 Choice('invite', 'Run a shared follow-up survey', 'A common method makes later comparisons stronger.')),
                'When is combining two datasets misleading?'),
        Chapter('A monitoring handoff', 'The project will outlast this team. Future volunteers need a protocol that preserves both evidence and uncertainty.',
                'What should they receive?',
                (Choice('guide', 'Write a repeatable protocol', 'The guide states measures, timing, and data limitations.'),
                 Choice('markers', 'Mark fixed survey locations', 'Future teams can compare the same places over time.')),
                'How will the next team know whether the intervention worked?'),
    ),
}

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


def view(world: str, chapter_index: int, beat: int, path: list[str], repair: bool = False,
         grade: int = 0) -> dict:
    chapters = SCHOLAR_STORIES[world] if grade >= 9 else ADVANCED_STORIES[world] if grade >= 4 else STORIES[world]
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
        advanced_titles = ('Examine the evidence', 'Make the idea clear', 'Work through the numbers')
        result['beat_title'] = 'Try another example' if repair and grade >= 4 else 'Try another clue' if repair else advanced_titles[beat] if grade >= 4 else BEATS[beat][1]
        result['beat_intro'] = ('The last clue was tricky. Try a different example of the same skill.'
                                if repair else ('Use the next activity to test your thinking about this chapter.'
                                                if grade >= 4 else BEAT_CUES[world][chapter_index][beat]))
    else:
        result['question'] = chapter.question
        result['choices'] = [{'id': choice.id, 'label': choice.label} for choice in chapter.choices]
    return result


def valid_choice(world: str, chapter_index: int, choice_id: str) -> bool:
    return 0 <= chapter_index < len(STORIES[world]) and any(
        choice.id == choice_id for choice in STORIES[world][chapter_index].choices
    )
