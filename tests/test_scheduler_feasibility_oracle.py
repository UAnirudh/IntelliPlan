"""Independent tiny-instance oracle for day allocation, plus clocked repairs.

The oracle enumerates all day assignments of five atomic 30-minute tasks. It
does not import planner constraints, costs, dependency layers or verification.
It proves feasibility only for this bounded domain, not global optimality.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from itertools import permutations, product
from random import Random

import pytest

from intelliplan.intelligence.planner import DayCapacity, PlannerTask, build_plan
from intelliplan.services.scheduling import SchedulingService, StudentContext

DAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 7)


@pytest.mark.parametrize('order', list(permutations(range(4))))
def test_descendant_deadline_propagates_through_chain_in_every_input_order(order):
    tasks = [
        PlannerTask(id='a', title='Research', est_minutes=30, calibrated=True,
                    due_date=DAY + timedelta(days=2)),
        PlannerTask(id='b', title='Draft', est_minutes=30, calibrated=True,
                    due_date=DAY + timedelta(days=2), depends_on=('a',)),
        PlannerTask(id='c', title='Submit', est_minutes=30, calibrated=True,
                    due_date=DAY, depends_on=('b',)),
        PlannerTask(id='x', title='Quiz', est_minutes=30, calibrated=True,
                    due_date=DAY + timedelta(days=1)),
    ]
    plan = build_plan([tasks[index] for index in order],
                      [DayCapacity(DAY, 90), DayCapacity(DAY + timedelta(days=1), 30),
                       DayCapacity(DAY + timedelta(days=2), 0)], today=DAY)
    assert not plan.deferred
    assert [(s.task_id, s.day) for s in plan.sessions] == [
        ('a', DAY), ('b', DAY), ('c', DAY), ('x', DAY + timedelta(days=1))]


def test_planner_relocates_flexible_sessions_to_fit_all_work():
    tomorrow = DAY + timedelta(days=1)
    later = DAY + timedelta(days=2)
    tasks = [
        PlannerTask(id='reading', title='Reading', est_minutes=25, due_date=later,
                    priority=60, calibrated=True),
        PlannerTask(id='quiz', title='Quiz', est_minutes=40, due_date=DAY,
                    priority=60, calibrated=True),
        PlannerTask(id='review', title='Review', est_minutes=25, due_date=tomorrow,
                    priority=60, calibrated=True),
        PlannerTask(id='worksheet', title='Worksheet', est_minutes=35, due_date=tomorrow,
                    priority=20, calibrated=True),
    ]
    plan = build_plan(tasks, [DayCapacity(DAY, 80), DayCapacity(tomorrow, 30),
                              DayCapacity(later, 50)], today=DAY)

    assert not plan.deferred
    assert {session.task_id: (session.day, session.minutes) for session in plan.sessions} == {
        'quiz': (DAY, 40),
        'worksheet': (DAY, 35),
        'review': (tomorrow, 25),
        'reading': (later, 25),
    }


@pytest.mark.parametrize(('rows', 'capacities', 'placed', 'deferred'), [
    ([('reading', 30, 10, 0, ()), ('essay', 60, 100, 0, ('reading',)),
      ('quiz', 30, 20, 0, ())], [60], {'reading', 'quiz'}, {'essay'}),
    ([('reading', 30, 1, 0, ()), ('essay', 30, 100, 0, ('reading',)),
      ('project', 30, 75, 1, ())], [60, 0], {'reading', 'essay'}, {'project'}),
    ([('quiz', 30, 45, 0, ()), ('review', 30, 45, 0, ()),
      ('project', 60, 85, 1, ())], [60, 0], {'quiz', 'review'}, {'project'}),
    ([('quiz', 30, 20, 0, ()), ('project', 30, 85, 1, ())],
     [30, 0], {'project'}, {'quiz'}),
])
def test_overload_triage_preserves_prerequisites_and_only_improves_value(
    rows, capacities, placed, deferred
):
    tasks = [PlannerTask(id=key, title=key, est_minutes=minutes, priority=priority,
                         calibrated=True, due_date=DAY + timedelta(days=due),
                         depends_on=parents, sittings=((None, minutes),))
             for key, minutes, priority, due, parents in rows]
    plan = build_plan(tasks, [DayCapacity(DAY + timedelta(days=offset), cap)
                              for offset, cap in enumerate(capacities)], today=DAY)
    assert {session.task_id for session in plan.sessions} == placed
    assert {item.task_id for item in plan.deferred} == deferred
    assert sum(session.minutes for session in plan.sessions) + sum(
        item.minutes for item in plan.deferred
    ) == sum(row[1] for row in rows)


def test_overload_triage_can_free_the_only_contiguous_clock_window():
    tomorrow = DAY + timedelta(days=1)
    windows = ((datetime(2026, 10, 19, 8), datetime(2026, 10, 19, 8, 20)),
               (datetime(2026, 10, 19, 10), datetime(2026, 10, 19, 10, 45)))
    review = PlannerTask(id='review', title='Review', est_minutes=30, priority=20,
                         calibrated=True, due_date=DAY, sittings=((None, 30),))
    project = PlannerTask(id='project', title='Project', est_minutes=35, priority=95,
                          calibrated=True, due_date=tomorrow, sittings=((None, 35),))
    plan = build_plan([review, project], [DayCapacity(DAY, 65, clock_windows=windows),
                                         DayCapacity(tomorrow, 0)], today=DAY)
    assert [(session.task_id, session.day, session.minutes)
            for session in plan.sessions] == [('project', DAY, 35)]
    assert [(item.task_id, item.minutes) for item in plan.deferred] == [('review', 30)]


def feasible_assignment(rows, capacities):
    """All declared work fits when one enumerated assignment obeys the inputs."""
    for assignment in product(range(len(capacities)), repeat=len(rows)):
        counts = Counter(assignment)
        if any(counts[day] > capacity for day, capacity in enumerate(capacities)):
            continue
        if any(not release <= assignment[index] <= due
               or any(assignment[parent] > assignment[index] for parent in parents)
               for index, (due, release, parents) in enumerate(rows)):
            continue
        return assignment
    return None


def test_day_allocation_matches_bounded_independent_feasibility_oracle():
    rng = Random(81037)
    missed, feasible_count = [], 0
    for case in range(2000):
        capacities = [rng.randint(0, 3) for _ in range(3)]
        rows = []
        for index in range(5):
            due = rng.randint(0, 2)
            release = rng.randint(0, due)
            parents = tuple(parent for parent in range(index) if rng.random() < 0.25)
            rows.append((due, release, parents))
        oracle = feasible_assignment(rows, capacities)
        tasks = [PlannerTask(
            id=str(index), title=str(index), est_minutes=30, calibrated=True,
            due_date=DAY + timedelta(days=due), not_before=DAY + timedelta(days=release),
            depends_on=tuple(str(parent) for parent in parents),
        ) for index, (due, release, parents) in enumerate(rows)]
        plan = build_plan(tasks, [DayCapacity(DAY + timedelta(days=d), cap * 30)
                                 for d, cap in enumerate(capacities)], today=DAY)
        # Check actual outputs against the inputs, without the production verifier.
        minutes, used, task_days = Counter(), Counter(), defaultdict(list)
        for session in plan.sessions:
            index = int(session.task_id)
            day = (session.day - DAY).days
            due, release, _ = rows[index]
            assert release <= day <= due, (case, session)
            minutes[index] += session.minutes
            used[day] += session.minutes
            task_days[index].append(day)
        deferred = Counter()
        for item in plan.deferred:
            deferred[int(item.task_id)] += item.minutes
            assert item.reason, case
        assert all(minutes[i] + deferred[i] == 30 for i in range(5)), case
        assert all(used[day] <= cap * 30 for day, cap in enumerate(capacities)), case
        for index, (_, _, parents) in enumerate(rows):
            if task_days[index]:
                assert all(minutes[parent] == 30 and max(task_days[parent]) <= min(task_days[index])
                           for parent in parents), case
        if oracle is not None:
            feasible_count += 1
            if plan.deferred:
                missed.append({'case': case, 'capacities': capacities, 'rows': rows, 'oracle': oracle})
        else:
            assert plan.deferred, case
    assert feasible_count == 261
    assert not missed, missed[:8]


@pytest.mark.parametrize('action', ['skip_day', 'limit_day'])
def test_clocked_repair_uses_real_deadline_after_losing_an_evening(action):
    service = SchedulingService(StudentContext(availability={
        day: {'start': '08:00', 'end': '11:00'} for day in ('Mon', 'Tue', 'Wed')
    }), decompose_stages=False)
    tasks = [
        PlannerTask(id='read', title='Read', est_minutes=30, calibrated=True,
                    due_date=DAY + timedelta(days=2)),
        PlannerTask(id='write', title='Write', est_minutes=30, calibrated=True,
                    due_date=DAY + timedelta(days=2), depends_on=('read',)),
        PlannerTask(id='quiz', title='Quiz', est_minutes=30, calibrated=True,
                    due_date=DAY + timedelta(days=1)),
    ]
    initial = build_plan(tasks, [DayCapacity(DAY + timedelta(days=d), 120) for d in range(3)], today=DAY)
    data = service.to_schedule_data(initial, now=NOW)
    payload = {'action': action, 'day': DAY.isoformat(), 'minutes': 30}
    _, adjusted = service.adjust(data, {}, payload, now=NOW, horizon_days=3)
    assert not adjusted['deferred']
    work = [block for day in adjusted['schedule'] for block in day['blocks'] if not block.get('is_break')]
    assert sum(block['duration_minutes'] for block in work) == 90
    read = [block for block in work if block['task_id'] == 'read']
    write = [block for block in work if block['task_id'] == 'write']
    assert max(datetime.fromisoformat(b['end_iso']) for b in read) <= min(
        datetime.fromisoformat(b['start_iso']) for b in write)
    if action == 'skip_day':
        assert all(not b['start_iso'].startswith(DAY.isoformat()) for b in work)
    assert service.verify(adjusted, now=NOW).feasible
