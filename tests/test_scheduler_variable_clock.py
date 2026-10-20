"""Independent witnesses for unequal sittings in fragmented clock windows.

The witness enumerates non-preemptive task orders; production may also split
new work, so absence of a witness is not a general infeasibility proof.
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
WINDOWS = tuple((datetime(2026, 10, 19 + d, h), datetime(2026, 10, 19 + d, h) + timedelta(minutes=90))
                for d in range(2) for h in (8, 18))


def service():
    return SchedulingService(StudentContext(
        availability={name: {'start': '08:00', 'end': '19:30'} for name in ('Mon', 'Tue')},
        busy_by_date={DAY + timedelta(days=d): [(570, 1080)] for d in range(2)},
    ), decompose_stages=False)


def render(tasks, *, limit=None):
    scheduler = service()
    if limit is not None:
        scheduler._overrides[DAY] = {'set': limit}
    prepared = scheduler._deadline_capacities(tasks, NOW)
    plan = build_plan(prepared, scheduler.capacities(DAY, 2, NOW), today=DAY, model=scheduler.model)
    return scheduler.to_schedule_data(plan, now=NOW)


def witness(tasks):
    for order in permutations(range(len(tasks))):
        done, cursor = {}, NOW
        for index in order:
            task = tasks[index]
            if any(parent not in done for parent in task.depends_on):
                break
            placed = None
            for lower, upper in WINDOWS:
                start = max(lower, cursor)
                end = start + timedelta(minutes=task.est_minutes)
                if end <= upper and end <= task.deadline_at:
                    placed = start, end
                    break
            if placed is None:
                break
            done[task.id] = placed
            cursor = placed[1] + timedelta(minutes=5)
        else:
            return done
    return None


def check_output(tasks, result):
    by_id = {task.id: task for task in tasks}
    minutes, deferred, intervals = Counter(), Counter(), defaultdict(list)
    for day in result['schedule']:
        for block in day['blocks']:
            if block.get('is_break'):
                continue
            task = by_id[block['task_id']]
            start = datetime.fromisoformat(block['start_iso'])
            end = datetime.fromisoformat(block['end_iso'])
            assert start >= NOW and (task.deadline_at is None or end <= task.deadline_at)
            assert any(lower <= start < end <= upper for lower, upper in WINDOWS)
            assert end - start == timedelta(minutes=block['duration_minutes'])
            minutes[task.id] += block['duration_minutes']
            intervals[task.id].append((start, end))
    for item in result['deferred']:
        assert item['reason']
        deferred[item['task_id']] += item['minutes']
    assert all(minutes[t.id] + deferred[t.id] == t.est_minutes for t in tasks)
    ordered = sorted(interval for group in intervals.values() for interval in group)
    assert all(start - end >= timedelta(minutes=5)
               for (_, end), (start, _) in zip(ordered, ordered[1:]))
    for task in tasks:
        if intervals[task.id]:
            assert all(minutes[parent] == by_id[parent].est_minutes
                       and max(end for _, end in intervals[parent]) <= min(start for start, _ in intervals[task.id])
                       for parent in task.depends_on)


@pytest.mark.parametrize('order', tuple(permutations(range(4))))
def test_longer_shared_deadline_uses_morning_shorter_work_uses_evening(order):
    tasks = [
        PlannerTask(id='0', title='Read A', est_minutes=25, calibrated=True,
                    due_date=DAY + timedelta(days=1), deadline_at=datetime(2026, 10, 20, 8, 40)),
        PlannerTask(id='1', title='Read B', est_minutes=25, calibrated=True,
                    due_date=DAY + timedelta(days=1), deadline_at=datetime(2026, 10, 20, 8, 20)),
        PlannerTask(id='2', title='Short response', est_minutes=15, calibrated=True,
                    due_date=DAY, deadline_at=datetime(2026, 10, 19, 18, 20), depends_on=('0', '1')),
        PlannerTask(id='3', title='Long response', est_minutes=25, calibrated=True,
                    due_date=DAY, deadline_at=datetime(2026, 10, 19, 18, 20), depends_on=('0', '1')),
    ]
    result = render([tasks[i] for i in order])
    check_output(tasks, result)
    assert not result['deferred']


def test_equal_long_sittings_use_real_clock_capacity_and_keep_overrides():
    tasks = [PlannerTask(id=str(i), title=str(i), est_minutes=40, calibrated=True,
                         due_date=DAY, deadline_at=datetime(2026, 10, 19, 19, 30)) for i in range(4)]
    result = render(tasks)
    check_output(tasks, result)
    assert not result['deferred']
    limited = render(tasks, limit=80)
    check_output(tasks, limited)
    assert sum(b['duration_minutes'] for d in limited['schedule'] if d['date'] == DAY.isoformat()
               for b in d['blocks'] if not b.get('is_break')) <= 80
    assert limited['deferred']


def test_date_only_tasks_also_obey_clock_windows_and_breaks():
    tasks = [PlannerTask(id=str(i), title=str(i), est_minutes=40, calibrated=True,
                         due_date=DAY) for i in range(5)]
    result = render(tasks)
    check_output(tasks, result)
    assert sum(item['minutes'] for item in result['deferred']) == 40


def test_different_preserved_part_sizes_keep_student_order():
    task = PlannerTask(id='saved', title='Saved work', est_minutes=40, calibrated=True,
                       due_date=DAY, deadline_at=datetime(2026, 10, 19, 9, 30),
                       sittings=((DAY, 15), (DAY, 25)))
    result = render([task])
    check_output([task], result)
    blocks = [b for d in result['schedule'] for b in d['blocks'] if not b.get('is_break')]
    assert [(b['part_index'], b['duration_minutes']) for b in blocks] == [(1, 15), (2, 25)]


def test_variable_duration_oracle_with_independent_output_checks():
    rng, feasible, misses = Random(148930), 0, []
    for case in range(500):
        tasks = []
        for index in range(4):
            offset = rng.randrange(2)
            hour, minute = rng.choice(((8, 20), (8, 40), (9, 0), (9, 30),
                                       (18, 20), (18, 40), (19, 0), (19, 30)))
            deadline = datetime(2026, 10, 19 + offset, hour, minute)
            duration = rng.choice((15, 25, 40))
            parents = tuple(str(p) for p in range(index) if rng.random() < 0.3)
            tasks.append(PlannerTask(id=str(index), title=str(index), est_minutes=duration,
                                     calibrated=True, due_date=deadline.date(),
                                     deadline_at=deadline, depends_on=parents))
        expected = witness(tasks)
        result = render(tasks)
        check_output(tasks, result)
        if expected is not None:
            feasible += 1
            if result['deferred']:
                misses.append(case)
    assert feasible == 320
    assert misses == [], misses
