"""Independent clock-slot witnesses for exact deadlines and prerequisites.

Four atomic 30-minute tasks, two 90-minute windows per day, two days. Each
window can hold at most two tasks with the required five-minute transition.
The oracle enumerates slot permutations without production scheduling helpers.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from itertools import permutations
from random import Random

import pytest

from intelliplan.intelligence.planner import PlannerTask, build_plan
from intelliplan.services.scheduling import SchedulingService, StudentContext

DAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 7)
SLOTS = tuple(datetime(2026, 10, 19 + offset, hour, minute)
              for offset in range(2) for hour, minute in ((8, 0), (8, 35), (18, 0), (18, 35)))


def service():
    return SchedulingService(StudentContext(
        availability={name: {'start': '08:00', 'end': '19:30'} for name in ('Mon', 'Tue')},
        busy_by_date={DAY + timedelta(days=offset): [(570, 1080)] for offset in range(2)},
    ), decompose_stages=False)


def render(tasks):
    scheduler = service()
    prepared = scheduler._deadline_capacities(tasks, NOW)
    plan = build_plan(prepared, scheduler.capacities(DAY, 2, NOW), today=DAY, model=scheduler.model)
    return scheduler.to_schedule_data(plan, now=NOW)


@pytest.mark.parametrize('order', tuple(permutations(range(4))))
def test_morning_dependent_lends_urgency_to_later_due_parent(order):
    tasks = [
        PlannerTask(id='read', title='Read source', est_minutes=30, calibrated=True,
                    due_date=DAY + timedelta(days=1), deadline_at=datetime(2026, 10, 20, 9, 5)),
        PlannerTask(id='other-a', title='Other work A', est_minutes=30, calibrated=True,
                    due_date=DAY, deadline_at=datetime(2026, 10, 19, 19, 30)),
        PlannerTask(id='other-b', title='Other work B', est_minutes=30, calibrated=True,
                    due_date=DAY, deadline_at=datetime(2026, 10, 19, 19, 30)),
        PlannerTask(id='write', title='Write response', est_minutes=30, calibrated=True,
                    due_date=DAY, deadline_at=datetime(2026, 10, 19, 9, 5), depends_on=('read',)),
    ]
    result = render([tasks[index] for index in order])
    assert not result['deferred']
    blocks = {b['task_id']: b for day in result['schedule'] for b in day['blocks'] if not b.get('is_break')}
    assert blocks['read']['start_iso'] == '2026-10-19T08:00:00'
    assert blocks['write']['end_iso'] == '2026-10-19T09:05:00'
    # Urgency is inherited internally; the assignment's own deadline is retained.
    assert blocks['read']['deadline_at'] == '2026-10-20T09:05:00'
    assert sum(b['duration_minutes'] for b in blocks.values()) == 120


def witness(tasks):
    for positions in permutations(range(len(SLOTS)), len(tasks)):
        if all(SLOTS[positions[index]] + timedelta(minutes=30) <= task.deadline_at
               and all(positions[int(parent)] < positions[index] for parent in task.depends_on)
               for index, task in enumerate(tasks)):
            return positions
    return None


def test_seeded_clock_oracle_checks_actual_timestamps_and_conserves_work():
    rng = Random(93817)
    feasible, missed = 0, []
    for case in range(500):
        tasks = []
        for index in range(4):
            offset = rng.randrange(2)
            hour, minute = rng.choice(((8, 30), (9, 5), (19, 30)))
            deadline = datetime(2026, 10, 19 + offset, hour, minute)
            parents = tuple(str(parent) for parent in range(index) if rng.random() < 0.3)
            tasks.append(PlannerTask(id=str(index), title=str(index), est_minutes=30, calibrated=True,
                                     due_date=deadline.date(), deadline_at=deadline, depends_on=parents))
        expected = witness(tasks)
        result = render(tasks)
        blocks = [b for day in result['schedule'] for b in day['blocks'] if not b.get('is_break')]
        minutes, intervals = Counter(), defaultdict(list)
        for block in blocks:
            key = int(block['task_id'])
            start, end = datetime.fromisoformat(block['start_iso']), datetime.fromisoformat(block['end_iso'])
            assert start >= NOW and end <= tasks[key].deadline_at, case
            assert int((end - start).total_seconds() // 60) == block['duration_minutes'], case
            assert ((start.hour >= 8 and end.hour <= 9 and end.hour * 60 + end.minute <= 570)
                    or (start.hour >= 18 and end.hour * 60 + end.minute <= 1170)), case
            intervals[key].append((start, end))
            minutes[key] += block['duration_minutes']
        ordered = sorted(interval for group in intervals.values() for interval in group)
        assert all(next_start - end >= timedelta(minutes=5)
                   for (_, end), (next_start, _) in zip(ordered, ordered[1:])), case
        deferred = Counter()
        for item in result['deferred']:
            deferred[int(item['task_id'])] += item['minutes']
        assert all(minutes[index] + deferred[index] == 30 for index in range(4)), case
        for index, task in enumerate(tasks):
            if intervals[index]:
                assert all(minutes[int(parent)] == 30 and max(e for _, e in intervals[int(parent)])
                           <= min(s for s, _ in intervals[index]) for parent in task.depends_on), case
        if expected is not None:
            feasible += 1
            if result['deferred']:
                missed.append((case, expected))
        else:
            assert result['deferred'], case
    assert feasible == 309
    assert not missed, missed[:8]
