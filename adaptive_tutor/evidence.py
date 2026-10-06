"""Inspectable recent-practice and delayed-review rules, not mastery estimates."""
from collections import deque
from datetime import timedelta


WINDOW = 6
INTERVALS = (0, 1, 3, 7, 14)


def summarize(attempts, now):
    """Reduce chronologically ordered rows with bounded memory per skill."""
    states = {}
    for attempt in attempts:
        state = states.setdefault(attempt['skill_id'], {
            'attempts': 0, 'correct': 0, 'independent_correct': 0,
            'recent': deque(maxlen=WINDOW * 2), 'stage': 0, 'due': None,
            'delayed_successes': 0,
        })
        at = attempt['created_at']
        independent = bool(attempt['correct'] and not attempt['assisted'])
        state['attempts'] += 1
        state['correct'] += int(bool(attempt['correct']))
        state['independent_correct'] += int(independent)
        state['recent'].append(independent)
        state['last_checked'] = at
        state['last_correct'] = bool(attempt['correct'])
        state['last_assisted'] = bool(attempt['assisted'])
        if not independent:
            state['stage'] = 0
            state['due'] = at
        elif state['stage'] == 0:
            state['stage'] = 1
            state['due'] = at + timedelta(days=1)
        elif at >= state['due']:
            state['stage'] = min(state['stage'] + 1, len(INTERVALS) - 1)
            state['delayed_successes'] += 1
            state['due'] = at + timedelta(days=INTERVALS[state['stage']])
        # Extra success before the due date changes recent evidence only.

    result = {}
    for skill_id, state in states.items():
        history = list(state['recent'])
        recent = history[-WINDOW:]
        trend = 'building'
        if len(history) == WINDOW * 2:
            difference = sum(recent) - sum(history[:WINDOW])
            trend = 'improving' if difference >= 2 else 'revisit' if difference <= -2 else 'steady'
        if now - state['last_checked'] >= timedelta(days=30):
            move, reason = 'review', 'It has been a while. Start with a fresh check of what you remember.'
        elif not state['last_correct']:
            move, reason = 'repair', 'The last answer missed the target. Work through a smaller example, then try again.'
        elif state['last_assisted']:
            move, reason = 'independent', 'The last answer used a hint. Try a new example without the hint.'
        elif state['due'] <= now:
            move, reason = 'review', 'A spaced review is due. Try recalling this skill before looking at an example.'
        elif len(recent) >= 3 and all(recent[-3:]):
            move, reason = 'transfer', 'Your last three answers were independently correct. Try applying the idea in a new situation.'
        elif len(recent) >= 3 and sum(recent) / len(recent) < .6:
            move, reason = 'repair', 'Recent answers are mixed. Revisit one small step and explain why it works.'
        else:
            move, reason = 'diagnose', 'Build a clearer picture with one question and an explanation of your reasoning.'
        result[skill_id] = {
            **{key: state[key] for key in ('attempts', 'correct', 'independent_correct', 'delayed_successes')},
            'recent_attempts': len(recent), 'recent_independent_correct': sum(recent),
            'trend': trend, 'next_move': move, 'next_reason': reason,
            'review_stage': state['stage'], 'review_due_at': state['due'].isoformat(),
            'last_checked_at': state['last_checked'].isoformat(),
        }
    return result
