"""Deterministic K-8 practice variants. No child answers or generated keys are stored.

Each item ID encodes a reviewed generator version, grade, strand, skill slot,
and index. A fixed ID always reconstructs the same prompt and answer.
"""

import re

from primer.models import Item, Skill


GRADE_LABELS = ('Kindergarten',) + tuple(f'Grade {grade}' for grade in range(1, 9))
MATH_TITLES = (
    ('Count to ten', 'Compare numbers', 'Join small groups'),
    ('Add within twenty', 'Subtract within twenty', 'Tens and ones'),
    ('Add two-digit numbers', 'Subtract from a hundred', 'Equal groups'),
    ('Multiply whole numbers', 'Divide into equal groups', 'Add within a thousand'),
    ('Multiply two-digit numbers', 'Equivalent fractions', 'Compare hundredths'),
    ('Add hundredths as fractions', 'Multiply decimals', 'Find rectangular volume'),
    ('Find a unit rate', 'Operate with integers', 'Evaluate expressions'),
    ('Reason with percent', 'Proportional relationships', 'Solve linear equations'),
    ('Apply exponent rules', 'Solve two-sided equations', 'Find slope'),
)
READ_TITLES = (
    ('Read a short account', 'Find a story clue'),
    ('Find a stated detail', 'Explain what happened'),
    ('Connect details', 'Use a text clue'),
    ('Find the central idea', 'Support an inference'),
    ('Summarize a paragraph', 'Cite a relevant detail'),
    ('Compare cause and effect', 'Choose the best evidence'),
    ('Trace an explanation', 'Distinguish claim and support'),
    ('Analyze a claim', 'Evaluate supporting evidence'),
    ('Follow a line of reasoning', 'Select precise evidence'),
)
WRITE_TITLES = (
    ('Capitalize a sentence', 'Build a complete sentence'),
    ('Use a matching verb', 'Revise a complete sentence'),
    ('Use past tense', 'Make a sentence precise'),
    ('Use commas in a series', 'Combine ideas clearly'),
    ('Punctuate dialogue', 'Revise for clarity'),
    ('Use a causal connection', 'Choose a precise transition'),
    ('Use pronoun case', 'Connect evidence to a claim'),
    ('Use parallel structure', 'Revise a reasoned sentence'),
    ('Choose active voice', 'Connect evidence and reasoning'),
)


def _skills():
    result = []
    for grade in range(9):
        for code, domain, titles in (
            ('r', 'Reading', READ_TITLES[grade]),
            ('w', 'Writing', WRITE_TITLES[grade]),
            ('m', 'Arithmetic', MATH_TITLES[grade]),
        ):
            for slot, title in enumerate(titles):
                skill_id = f'g{grade}{code}{slot}'
                prior = f'g{grade}{code}{slot - 1}' if slot else None
                result.append(Skill(skill_id, domain, title, prior, grade))
    return tuple(result)


GENERATED_SKILLS = _skills()
GENERATED_SKILL_BY_ID = {skill.id: skill for skill in GENERATED_SKILLS}
_ITEM_ID = re.compile(r'^v1g([0-8])([rwm])([0-2])-([0-9]{5})$')


def generated_item_count(skill_id: str) -> int:
    skill = GENERATED_SKILL_BY_ID.get(skill_id)
    if not skill:
        return 0
    if skill.domain != 'Arithmetic':
        return 1000
    if skill.grade == 0:
        return (10, 100, 66)[int(skill_id[-1])]
    if skill.grade == 1:
        return (231, 231, 90)[int(skill_id[-1])]
    if skill.grade == 2 and skill_id.endswith('2'):
        return 25
    if skill.grade == 3 and not skill_id.endswith('2'):
        return 144
    if skill.grade == 4 and skill_id.endswith('1'):
        return 900
    return 10000


def item_for_skill(skill_id: str, index: int) -> Item:
    """Build an item by its stable index; callers must bound the index."""
    skill = GENERATED_SKILL_BY_ID[skill_id]
    count = generated_item_count(skill_id)
    if type(index) is not int or not 0 <= index < count:
        raise ValueError('Invalid generated item index')
    slot = int(skill_id[-1])
    item_id = f'v1{skill_id}-{index:05d}'
    if skill.domain == 'Arithmetic':
        prompt, answer, options, hint = _math(skill.grade, slot, index)
    elif skill.domain == 'Reading':
        prompt, answer, options, hint = _reading(skill.grade, slot, index)
    else:
        prompt, answer, options, hint = _writing(skill.grade, slot, index)
    return Item(item_id, skill_id, prompt, answer, options, hint,
                f'The answer is {answer}.')


def generated_item(item_id: str) -> Item | None:
    match = _ITEM_ID.fullmatch(item_id)
    if not match:
        return None
    grade, code, slot, index = match.groups()
    skill_id = f'g{grade}{code}{slot}'
    number = int(index)
    if number >= generated_item_count(skill_id):
        return None
    return item_for_skill(skill_id, number)


def _math(grade: int, slot: int, index: int):
    a, b = divmod(index, 100)
    if grade == 0:
        if slot == 0:
            n = index + 1
            return f'Count: {"● " * n}How many dots?', str(n), (), 'Touch each dot once.'
        if slot == 1:
            x, y = divmod(index, 10)
            answer = 'equal' if x == y else str(max(x + 1, y + 1))
            return f'Which number is greater: {x + 1} or {y + 1}? If they match, say equal.', answer, (), 'Count up to each number.'
        x, y = _pair_with_limit(index, 10)
        return f'You have {x} stones and find {y} more. How many stones?', str(x + y), (), 'Count on from the first group.'
    if grade == 1:
        if slot == 0:
            x, y = _pair_with_limit(index, 20)
            return f'What is {x} + {y}?', str(x + y), (), 'Count on or make a ten.'
        if slot == 1:
            x, y = _subtraction_pair(index, 20)
            return f'What is {x} - {y}?', str(x - y), (), 'Count back from the first number.'
        n = index + 10
        return f'In {n}, how many tens are there?', str(n // 10), (), 'Group the number into tens and ones.'
    if grade == 2:
        if slot == 0:
            return f'What is {a} + {b}?', str(a + b), (), 'Add the ones, then the tens.'
        if slot == 1:
            return f'What is {100 + a} - {b}?', str(100 + a - b), (), 'Subtract the ones, then the tens.'
        x, y = divmod(index, 5)
        return f'There are {x + 1} equal groups with {y + 1} counters each. How many counters in all?', str((x + 1) * (y + 1)), (), 'Add the equal groups or skip count.'
    if grade == 3:
        if slot == 0:
            x, y = divmod(index, 12)
            return f'What is {x + 1} × {y + 1}?', str((x + 1) * (y + 1)), (), 'Think of equal groups.'
        if slot == 1:
            x, y = divmod(index, 12)
            return f'What is {(x + 1) * (y + 1)} ÷ {x + 1}?', str(y + 1), (), 'Use the related multiplication fact.'
        return f'What is {100 + a} + {100 + b}?', str(200 + a + b), (), 'Add ones, tens, and hundreds.'
    if grade == 4:
        if slot == 0:
            return f'What is {a + 10} × {b + 10}?', str((a + 10) * (b + 10)), (), 'Use partial products.'
        if slot == 1:
            numerator, factor = divmod(index, 30)
            numerator += 1
            factor += 2
            return (f'Which fraction is equivalent to {numerator}/{factor}?',
                    f'{numerator * 2}/{factor * 2}',
                    (f'{numerator * 2}/{factor * 2}', f'{numerator + 1}/{factor}', f'{numerator}/{factor + 1}'),
                    'Multiply both numerator and denominator by the same number.')
        left, right = f'{a / 100:.2f}', f'{b / 100:.2f}'
        answer = '>' if a > b else '<' if a < b else '='
        return f'Compare {left} and {right}. Which symbol belongs between them?', answer, ('<', '=', '>'), 'Compare tenths, then hundredths.'
    if grade == 5:
        if slot == 0:
            return (f'What is {a + 1}/100 + {b + 1}/100?', f'{a + b + 2}/100', (),
                    'The denominators match; add the numerators.')
        if slot == 1:
            return f'What is {a / 10:.1f} × {b / 10:.1f}?', f'{a * b / 100:.2f}', (), 'Multiply as whole numbers, then place the decimal.'
        return f'A box is {a + 1} cm long, {b + 1} cm wide, and 2 cm high. What is its volume in cubic centimeters?', str((a + 1) * (b + 1) * 2), (), 'Length × width × height gives volume.'
    if grade == 6:
        if slot == 0:
            return f'{a + 1} tickets cost ${(a + 1) * (b + 1)}. What is the cost per ticket in dollars?', str(b + 1), (), 'Divide total cost by the number of tickets.'
        if slot == 1:
            return f'What is ({a - 50}) + ({b - 50})?', str(a + b - 100), (), 'Use a number line to combine signed values.'
        return f'If x = {a}, what is {b + 1}x + 3?', str((b + 1) * a + 3), (), 'Replace x, then multiply before adding.'
    if grade == 7:
        if slot == 0:
            return f'What is {a + 1}% of {100 * (b + 1)}?', str((a + 1) * (b + 1)), (), 'Percent means per hundred.'
        if slot == 1:
            return f'A pattern follows y = {a + 1}x. What is y when x = {b + 1}?', str((a + 1) * (b + 1)), (), 'Substitute the given x value.'
        return f'Solve: x + {a + 1} = {a + b + 2}. What is x?', str(b + 1), (), 'Subtract the same value from both sides.'
    if slot == 0:
        return f'Simplify the exponent in 2^{a + 1} × 2^{b + 1} = 2^?. What exponent replaces ?', str(a + b + 2), (), 'Add exponents when multiplying powers with the same base.'
    if slot == 1:
        x = b + 1
        return f'Solve: {a + 2}x + 3 = x + {(a + 1) * x + 3}. What is x?', str(x), (), 'Collect x terms on one side.'
    return f'A line rises {(a + 1) * (b + 1)} units while it runs {a + 1} units right. What is its slope?', str(b + 1), (), 'Slope is rise divided by run.'


def _pair_with_limit(index: int, limit: int) -> tuple[int, int]:
    for first in range(limit + 1):
        width = limit - first + 1
        if index < width:
            return first, index
        index -= width
    raise ValueError('Pair index is out of range')


def _subtraction_pair(index: int, limit: int) -> tuple[int, int]:
    for first in range(limit + 1):
        if index <= first:
            return first, index
        index -= first + 1
    raise ValueError('Subtraction index is out of range')


NAMES = ('Ari', 'Ben', 'Cora', 'Dina', 'Eli', 'Faye', 'Gus', 'Hana', 'Ira', 'Jules')
MOMENTS = ('On Monday', 'On Tuesday', 'On Wednesday', 'On Thursday', 'On Friday',
           'One morning', 'One afternoon', 'After lunch', 'At the start of the day', 'Later that week')
EARLY_EVENTS = (
    ('a seed', 'in the garden', 'gave it water', 'a sprout grew', 'water'),
    ('a ball', 'in the park', 'rolled it down a hill', 'it went far', 'the hill'),
    ('a lamp', 'in a room', 'turned it on', 'the room was bright', 'the lamp'),
    ('a book', 'at the library', 'read it aloud', 'the group heard a story', 'reading aloud'),
    ('a plant', 'near a window', 'moved it into the light', 'new leaves grew', 'sunlight'),
    ('a kite', 'in a field', 'waited for wind', 'it flew up', 'the wind'),
    ('a boat', 'by a pond', 'pushed it into the water', 'it floated', 'the water'),
    ('a bell', 'in a classroom', 'rang it', 'everyone heard a sound', 'the bell'),
    ('a box', 'on a shelf', 'opened it', 'a toy was inside', 'opening the box'),
    ('a map', 'on a table', 'followed its path', 'the group found the gate', 'the path'),
)
EVENTS = (
    ('a seed', 'in the garden', 'watered it', 'a green shoot appeared', 'water'),
    ('a map', 'by the trail', 'followed its marked path', 'the bridge came into view', 'the marked path'),
    ('a lantern', 'in a workshop', 'replaced its battery', 'the lantern shone again', 'a new battery'),
    ('a basket', 'at the market', 'patched a hole in it', 'the apples stayed inside', 'the patch'),
    ('a kite', 'in the park', 'waited for the wind', 'the kite rose high', 'the wind'),
    ('a book', 'at the library', 'read its instructions', 'the model fit together', 'the instructions'),
    ('a boat', 'at the harbor', 'secured its loose rope', 'the boat stayed by the dock', 'the rope'),
    ('a plant', 'near a window', 'moved it into sunlight', 'new leaves grew', 'sunlight'),
    ('a clock', 'in a workshop', 'set its hands correctly', 'it showed the right time', 'the corrected hands'),
    ('a sign', 'at the forest edge', 'checked its directions', 'the group reached the lookout', 'the directions'),
)


def _reading(grade: int, slot: int, index: int):
    name = NAMES[index // 100]
    moment = MOMENTS[(index // 10) % 10]
    thing, location, action, outcome, reason = (EARLY_EVENTS if grade <= 1 else EVENTS)[index % 10]
    first = f'{moment}, {name} found {thing} {location}.'
    second = f'{name} {action}, and {outcome}.'
    if grade <= 1:
        passage = f'{first} {second}'
    elif grade <= 3:
        passage = f'{first} At first, the next step was not clear. {second}'
    elif grade <= 5:
        passage = f'{first} The first attempt did not settle the problem, so {name} considered what might help. {second}'
    else:
        passage = (f'{first} Although an observer expected an immediate result, {name} first examined the situation. '
                   f'{second} This sequence suggests that a deliberate step mattered more than guessing.')
    if slot == 0:
        question = 'Which object or place is stated at the start?' if grade < 4 else 'Which detail is explicitly stated in the account?'
        return (f'Read: "{passage}" {question}', thing,
                (thing, 'a telescope', 'a bicycle'), 'Return to the first sentence and find the named object.')
    question = ('What helped the outcome happen?' if grade < 4 else
                'Which detail best supports the conclusion that the deliberate step mattered?')
    return (f'Read: "{passage}" {question}', reason,
            (reason, 'a lucky guess', 'someone else doing the work'),
            'Find the action just before the result.')


SUBJECTS = ('fox', 'bird', 'child', 'rabbit', 'artist', 'student', 'sailor', 'farmer', 'pilot', 'gardener')
VERBS = (
    ('finds', 'found', 'find'), ('carries', 'carried', 'carry'), ('moves', 'moved', 'move'),
    ('studies', 'studied', 'study'), ('checks', 'checked', 'check'), ('paints', 'painted', 'paint'),
    ('holds', 'held', 'hold'), ('opens', 'opened', 'open'), ('places', 'placed', 'place'),
    ('follows', 'followed', 'follow'),
)
OBJECTS = ('map', 'book', 'shell', 'stone', 'note', 'kite', 'basket', 'model', 'flower', 'letter')


def _writing(grade: int, slot: int, index: int):
    subject = SUBJECTS[index // 100]
    present, past, bare = VERBS[(index // 10) % 10]
    obj = OBJECTS[index % 10]
    base = f'The {subject} {present} the {obj}.'
    if slot == 1:
        if grade < 5:
            return (f'Choose the complete, clear sentence about the {subject} and {obj}.', base,
                    (base, f'The {subject} the {obj}.', f'{present.capitalize()} the {obj} the {subject}.'),
                    'A complete sentence names who did something and what happened.')
        answer = f'Because the {subject} {past} the {obj}, the task was finished.'
        return (f'Choose the sentence that clearly connects the action and result for the {subject} and {obj}.',
                answer, (answer, f'The {subject} {past} the {obj}, the task was finished, because.',
                         f'The task was finished the {subject} {past} the {obj}.'),
                'Use a complete clause to explain the reason for the result.')
    if grade == 0:
        return f'Rewrite with a capital and a period: "the {subject} {present} the {obj}"', base, (), 'Start with a capital and end with a period.'
    if grade == 1:
        return f'Complete: "The {subject} ___ the {obj}."', present, (present, bare, past), 'One subject takes the matching present-tense verb.'
    if grade == 2:
        return f'Complete: "Yesterday, the {subject} ___ the {obj}."', past, (past, present, bare), 'Yesterday calls for a past-tense verb.'
    if grade == 3:
        answer = f'The {subject} {present} the {obj}, a pen, and a cup.'
        return (f'Choose the sentence with commas in a series about the {subject}.', answer,
                (answer, f'The {subject} {present} the {obj} a pen and a cup.',
                 f'The {subject}, {present} the {obj}, a pen and a cup.'), 'Separate items in a series with commas.')
    if grade == 4:
        answer = f'"I {bare} the {obj}," said the {subject}.'
        return (f'Choose correctly punctuated dialogue about the {subject} and {obj}.', answer,
                (answer, f'"I {bare} the {obj}" said the {subject}.',
                 f'I {bare} the {obj}, said the {subject}.'), 'Put spoken words in quotation marks and the comma inside them.')
    if grade == 5:
        answer = f'Because the {subject} {past} the {obj}, the task was finished.'
        return (f'Choose the sentence that explains why the task was finished.', answer,
                (answer, f'Although the {subject} {past} the {obj}, the task was finished.',
                 f'The {subject} {past} the {obj} because.'), 'Choose the connection that shows cause.')
    if grade == 6:
        answer = f'The {subject} and I {past} the {obj}.'
        return (f'Choose the correct subject pronoun in a sentence about the {obj}.', answer,
                (answer, f'The {subject} and me {past} the {obj}.',
                 f'The {subject} and myself {past} the {obj}.'), 'Use I for the person doing the action.')
    if grade == 7:
        answer = f'The {subject} likes to {bare} the {obj} and to check the plan.'
        return (f'Choose the sentence with parallel actions for the {subject}.', answer,
                (answer, f'The {subject} likes to {bare} the {obj} and checking the plan.',
                 f'The {subject} likes {bare} the {obj} and to check the plan.'),
                'Keep both actions in the same grammatical form.')
    answer = f'The {subject} {past} the {obj}.'
    return (f'Choose the active-voice sentence that emphasizes who acted.', answer,
            (answer, f'The {obj} was {past} by the {subject}.',
             f'The {obj} was involved with the {subject}.'), 'Place the actor before the action.')
