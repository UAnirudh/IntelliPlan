"""Prerequisites remain executable before dependent work is offered."""
from datetime import date, datetime, timedelta

import pytest

from intelliplan.intelligence.planner import PlannerTask, DayCapacity, build_plan
from intelliplan.intelligence.rescheduling import snapshot_from_schedule
from intelliplan.services.scheduling import SchedulingService, StudentContext

DAY = date(2026, 10, 13)


def tasks():
    return [PlannerTask(id='a', title='Read source material', est_minutes=60, priority=10,
                        due_date=DAY + timedelta(days=1)),
            PlannerTask(id='b', title='Write response', est_minutes=30, priority=100,
                        due_date=DAY, depends_on=('a',))]


@pytest.mark.parametrize('capacity', [30, 60])
def test_dependent_is_deferred_when_prerequisite_does_not_fully_fit(capacity):
    plan = build_plan(tasks(), [DayCapacity(DAY, capacity)], today=DAY)
    assert not any(s.task_id == 'b' for s in plan.sessions)
    assert sum(s.minutes for s in plan.sessions) + sum(d.minutes for d in plan.deferred) == 90
    assert next(d for d in plan.deferred if d.task_id == 'b').depends_on == ('a',)


def test_same_day_prerequisite_precedes_more_urgent_dependent():
    plan = build_plan(tasks(), [DayCapacity(DAY, 90)], today=DAY)
    assert not plan.deferred
    ids = [s.task_id for s in plan.sessions]
    assert max(i for i,t in enumerate(ids) if t == 'a') < min(i for i,t in enumerate(ids) if t == 'b')


def test_saved_repair_preserves_explicit_dependency_edges():
    now = datetime(2026, 10, 13, 7)
    service = SchedulingService(StudentContext(availability={'Tue': {'start':'08:00','end':'12:00'}}),
                                decompose_stages=False)
    plan = build_plan(tasks(), [DayCapacity(DAY, 180)], today=DAY)
    data = service.to_schedule_data(plan, now=now)
    work = [b for d in data['schedule'] for b in d['blocks'] if not b.get('is_break')]
    assert next(b for b in work if b['task_id'] == 'b')['depends_on'] == ['a']
    snap = snapshot_from_schedule(data, {}, DAY)
    assert next(t for t in snap.tasks if t.id == 'b').depends_on == ('a',)
    _, adjusted = service.adjust(data, {}, {'action':'limit_day','day':DAY.isoformat(),'minutes':30}, now=now)
    assert not any(b.get('task_id') == 'b' for d in adjusted['schedule'] for b in d['blocks'])
    assert next(d for d in adjusted['deferred'] if d['task_id'] == 'b')['depends_on'] == ['a']
    again = snapshot_from_schedule(adjusted, {}, DAY)
    assert next(t for t in again.tasks if t.id == 'b').depends_on == ('a',)


def test_clock_spill_of_prerequisite_defers_dependent_and_verifier_catches_manual_reversal():
    from intelliplan.intelligence.constraints import check_schedule
    from intelliplan.services.scheduling import plan_to_schedule_data
    plan = build_plan(tasks(), [DayCapacity(DAY, 90)], today=DAY)
    data = plan_to_schedule_data(plan, availability={'Tue': {'start':'08:00','end':'08:45'}},
                                 now=datetime(2026,10,13,7))
    assert not any(b.get('task_id') == 'b' for d in data['schedule'] for b in d['blocks'])
    assert sum(d['minutes'] for d in data['deferred']) + sum(
        b['duration_minutes'] for d in data['schedule'] for b in d['blocks'] if not b.get('is_break')) == 90
    bad = {'schedule':[{'date':DAY.isoformat(),'blocks':[
        {'task_id':'b','depends_on':['a'],'assignment':'Write','duration_minutes':30,
         'start_iso':'2026-10-13T08:00:00','end_iso':'2026-10-13T08:30:00'},
        {'task_id':'a','assignment':'Read','duration_minutes':60,
         'start_iso':'2026-10-13T09:00:00','end_iso':'2026-10-13T10:00:00'}]}]}
    assert any(v.key == 'prerequisite_unready' for v in check_schedule(bad).hard)
