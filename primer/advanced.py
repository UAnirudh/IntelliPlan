"""Versioned high-school and college-foundation practice.

The bank is deterministic: an ID reconstructs the same bounded question and
answer. The questions assess narrow skills; original writing needs a human or
separately validated rubric and is intentionally outside this bank.
"""

import re

from primer.models import Item, Skill


LEVEL_LABELS = {9: 'Grade 9', 10: 'Grade 10', 11: 'Grade 11',
                12: 'Grade 12', 13: 'College foundation'}
TITLES = {
    9: {'r': ('Read a data claim', 'Spot an unsupported conclusion'),
        'w': ('Qualify a claim', 'Connect a claim to evidence'),
        'm': ('Solve a linear equation', 'Solve a two-variable system')},
    10: {'r': ('Compare two samples', 'Check a causal claim'),
         'w': ('Revise a comparison', 'Attribute a finding'),
         'm': ('Find quadratic roots', 'Use the Pythagorean theorem')},
    11: {'r': ('Interpret a trend', 'Find a study limitation'),
         'w': ('State a trend precisely', 'Name a limitation'),
         'm': ('Compose linear functions', 'Extend a geometric sequence')},
    12: {'r': ('Evaluate a summary', 'Separate correlation and cause'),
         'w': ('Write a bounded conclusion', 'Distinguish result and inference'),
         'm': ('Calculate probability', 'Find a weighted mean')},
    13: {'r': ('Read a research snapshot', 'Evaluate generalizability'),
         'w': ('Synthesize a result', 'Write a cautious implication'),
         'm': ('Compare unit rates', 'Model a percent change')},
}
TOPICS = ('library visits', 'study sessions', 'bus journeys', 'garden plots',
          'water samples', 'reading logs', 'practice trials', 'energy checks',
          'community surveys', 'lab observations')


def _skills():
    result = []
    for level, domains in TITLES.items():
        for code, domain in (('r', 'Reading'), ('w', 'Writing'), ('m', 'Arithmetic')):
            for slot, title in enumerate(domains[code]):
                result.append(Skill(f'g{level}{code}{slot}', domain, title,
                                    f'g{level}{code}0' if slot else None, level))
    return tuple(result)


ADVANCED_SKILLS = _skills()
ADVANCED_SKILL_BY_ID = {skill.id: skill for skill in ADVANCED_SKILLS}
_ID = re.compile(r'^v2g(9|10|11|12|13)([rwm])([01])-([0-9]{4})$')
ITEMS_PER_SKILL = 1000


def advanced_item_count(skill_id: str) -> int:
    return ITEMS_PER_SKILL if skill_id in ADVANCED_SKILL_BY_ID else 0


def advanced_item_for_skill(skill_id: str, index: int) -> Item:
    if skill_id not in ADVANCED_SKILL_BY_ID or type(index) is not int or not 0 <= index < ITEMS_PER_SKILL:
        raise ValueError('Invalid advanced item index')
    skill = ADVANCED_SKILL_BY_ID[skill_id]
    slot = int(skill_id[-1])
    if skill.domain == 'Arithmetic':
        prompt, answer, options, hint, explanation = _math(skill.grade, slot, index)
    elif skill.domain == 'Reading':
        prompt, answer, options, hint, explanation = _reading(skill.grade, slot, index)
    else:
        prompt, answer, options, hint, explanation = _writing(skill.grade, slot, index)
    if options:
        offset = (index * 7 + skill.grade + slot + len(skill.domain)) % len(options)
        options = options[offset:] + options[:offset]
    return Item(f'v2{skill_id}-{index:04d}', skill_id, prompt, answer,
                options, hint, explanation)


def advanced_item(item_id: str) -> Item | None:
    match = _ID.fullmatch(item_id) if isinstance(item_id, str) else None
    if not match:
        return None
    level, code, slot, index = match.groups()
    if int(index) >= ITEMS_PER_SKILL:
        return None
    return advanced_item_for_skill(f'g{level}{code}{slot}', int(index))


def _evidence(index: int):
    topic = TOPICS[index % len(TOPICS)]
    n = 20 + index // 10
    observed = 5 + (index * 7) % (n - 5)
    place = ('one school', 'one campus', 'one neighborhood', 'one lab', 'one program')[index // 2 % 5]
    return topic, n, observed, place


def _case(level: int, index: int):
    topic, n, observed, place = _evidence(index)
    comparison = max(1, observed - 1 - index % 4)
    if level == 9:
        record = f'A record from {place} counted {n} {topic}; {observed} met the stated target.'
        supported = f'{observed} of the {n} recorded {topic} met the target in {place}.'
        limit = f'The record covers only {place}; it cannot establish what happens everywhere.'
        next_step = f'Check a second setting before extending this {place} result.'
    elif level == 10:
        record = (f'Two groups at {place} each recorded {n} {topic}. Group A had {observed} meet the target; '
                  f'Group B had {comparison} meet it. The groups were not randomly assigned.')
        supported = f'Group A had {observed} target cases, more than Group B\'s {comparison}, among {n} in each group.'
        limit = 'The groups were not randomly assigned, so their difference does not establish a cause.'
        next_step = 'Check how the groups were selected before explaining the difference.'
    elif level == 11:
        record = (f'At {place}, a first count found {comparison} of {n} {topic} met the target; '
                  f'a later count found {observed} of {n}. The method was the same on both dates.')
        supported = f'The recorded count rose from {comparison} to {observed} out of {n} between the two dates.'
        limit = 'Two dates show a change, but they cannot by themselves establish a lasting trend.'
        next_step = 'Repeat the count on more dates before claiming a long-term trend.'
    elif level == 12:
        record = (f'An observational comparison at {place} followed two groups of {n} {topic}: '
                  f'{observed} in the first group and {comparison} in the second met the target.')
        supported = f'The first group had {observed} target cases versus {comparison} in the second group, with {n} per group.'
        limit = 'An observational difference alone cannot establish that group membership caused the outcome.'
        next_step = 'Investigate other differences between the groups before making a causal claim.'
    else:
        record = (f'A research snapshot sampled {n} {topic} from {place}; {observed} met a predefined target. '
                  'The sample was recruited from volunteers at that one setting.')
        supported = f'In the volunteer sample from {place}, {observed} of {n} met the predefined target.'
        limit = 'One volunteer sample may not represent the wider population or other settings.'
        next_step = 'Recruit a broader sample before applying this result elsewhere.'
    return record, supported, limit, next_step


def _reading(level: int, slot: int, index: int):
    source, supported, limit, _ = _case(level, index)
    if slot == 0:
        return (f'Read: "{source}" Which conclusion follows from this evidence?', supported,
                (supported, 'The result applies to everyone in every setting.',
                 'The record establishes that one factor caused the outcome.'),
                'Keep the conclusion within the counts, groups, and setting given.',
                f'The supported conclusion is bounded by the record: {supported}')
    return (f'Read: "{source}" What limits a stronger conclusion?', limit,
            (limit, 'The numbers prove the proposed explanation.',
             'No additional observations could change the conclusion.'),
            'Consider sampling, comparison, time, and possible alternative explanations.',
            f'This is the relevant limit: {limit}')


def _writing(level: int, slot: int, index: int):
    source, supported, _, next_step = _case(level, index)
    if slot == 0:
        return (f'Choose a precise sentence for this evidence: "{source}"',
                supported, (supported, 'This proves the same result holds everywhere.',
                            'The evidence proves the outcome was caused by the setting.'),
                'State the observed result, its denominator or comparison, and its scope.',
                f'This sentence reports the observation without overclaiming: {supported}')
    return (f'Choose a useful next sentence after this result: "{source}"',
            next_step, (next_step, 'This one record proves the pattern is universal.',
                     'No additional evidence could change the conclusion.'),
            'A useful next sentence identifies a test or missing evidence.',
            f'The next step follows the limitation of this evidence: {next_step}')


def _math(level: int, slot: int, index: int):
    a, b = divmod(index, 100)
    x, y = a + 2, b + 1
    if level == 9:
        if slot == 0:
            coefficient = 2 + a
            return (f'Solve {coefficient}x + {y} = {coefficient * x + y}. What is x?',
                    str(x), (), 'Subtract the constant, then divide by the coefficient.',
                    f'{coefficient}x = {coefficient * x}, so x = {x}.')
        return (f'Solve x + y = {x + y} and x - y = {x - y}. What is x?', str(x), (),
                'Add the equations to eliminate y.', f'2x = {2 * x}, so x = {x}.')
    if level == 10:
        if slot == 0:
            return (f'One root of (x - {x})(x + {y}) = 0 is {x}. What is the other root?',
                    str(-y), (), 'Set each factor equal to zero.', f'x + {y} = 0 gives x = {-y}.')
        return (f'There are {y} identical right triangles, each with legs {3 * x} and {4 * x}. What is the combined length of their hypotenuses?',
                str(5 * x * y), (), 'Find one hypotenuse with a² + b² = c², then multiply.',
                f'Each is a scaled 3–4–5 triangle with hypotenuse {5 * x}; {y} total {5 * x * y}.')
    if level == 11:
        if slot == 0:
            return (f'Let f(t) = {x}t + {y} and g(t) = t + {a + 1}. What is f(g({b}))?',
                    str(x * (b + a + 1) + y), (), 'Evaluate the inner function first.',
                    f'g({b}) = {b + a + 1}; substitute that result into f.')
        return (f'A geometric sequence starts at {x} and doubles each step. What is term {y + 2}?',
                str(x * 2 ** (y + 1)), (), 'Term one is the starting value; count the doublings.',
                f'Term {y + 2} is {x} × 2^{y + 1}.')
    if level == 12:
        if slot == 0:
            return (f'A bag contains {x} blue and {y} gold counters. What fraction is the probability of drawing blue?',
                    f'{x}/{x + y}', (), 'Favorable outcomes divided by all outcomes.',
                    f'There are {x} blue counters out of {x + y} total.')
        return (f'A course uses a 40% project score of {x * 5} and a 60% exam score of {y * 5}. What is the weighted score?',
                str(2 * x + 3 * y), (), 'Multiply each score by its weight, then add.',
                f'0.4 × {x * 5} + 0.6 × {y * 5} = {2 * x + 3 * y}.')
    if slot == 0:
        a_rate, b_rate = y / 2, (y + 1) / 3
        choice = 'Same cost' if 2 * (y + 1) == 3 * y else 'B' if b_rate < a_rate else 'A'
        return (f'Plan A offers {2 * x} units for ${x * y}. Plan B offers {3 * x} units for ${x * (y + 1)}. Which has the lower cost per unit, or are they the same?',
                choice, ('A', 'B', 'Same cost'),
                'Divide each total cost by its number of units.',
                f'A costs ${a_rate:.2f} per unit; B costs ${b_rate:.2f} per unit.')
    return (f'A price of ${10 * x} rises by {y}%. What is the new price in dollars?',
            str(10 * x + x * y / 10).rstrip('0').rstrip('.') if x * y % 10 else str(10 * x + x * y // 10),
            (), 'Find the percentage of the original price and add it.',
            f'The increase is ${x * y / 10:g}; add it to ${10 * x}.')
