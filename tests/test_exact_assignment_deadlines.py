"""An explicit morning deadline must never become an end-of-day deadline."""
from datetime import date, datetime

import pytest

import scheduler_engine
from intelliplan.intelligence.constraints import check_schedule
from intelliplan.intelligence.deadlines import local_deadline
from intelliplan.intelligence.planner import DayCapacity, PlannerTask, build_plan, task_from_mapping
from intelliplan.intelligence.rescheduling import snapshot_from_schedule, plan_from_sittings
from intelliplan.services.scheduling import SchedulingService, StudentContext, plan_to_schedule_data


DAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 7)


@pytest.mark.parametrize(('value', 'expected'), [
    ('2026-10-14T16:00:00Z', datetime(2026, 10, 14, 9)),
    ('2026-10-14T01:00:00+00:00', datetime(2026, 10, 13, 18)),
    ('2026-10-14T09:00:00', datetime(2026, 10, 14, 9)),
    ('2026-10-14', None), (DAY, None), ('invalid', None),
])
def test_explicit_instants_use_student_zone_but_date_only_stays_date_only(value, expected):
    assert local_deadline(value, 'America/Los_Angeles') == expected


def test_task_adapter_keeps_utc_crossing_deadline_day_and_date_only_behavior():
    row = {'id': 'a', 'title': 'Quiz', 'due_date': '2026-10-14', 'due_at': '2026-10-14T01:00:00Z'}
    task = task_from_mapping(row, timezone_name='America/Los_Angeles')
    assert task.due_date == date(2026, 10, 13)
    assert task.deadline_at == datetime(2026, 10, 13, 18)
    assert task_from_mapping({'title': 'Untimed', 'due_date': '2026-10-14'}).deadline_at is None


def test_clock_placer_defers_work_that_would_finish_after_exact_deadline():
    window = scheduler_engine.Window(datetime(2026, 10, 14, 8), datetime(2026, 10, 14, 12))
    block = {'id': 'a', 'assignment': 'Quiz', 'duration_minutes': 60,
             'deadline_at': '2026-10-14T08:59:59'}
    placed, overflow = scheduler_engine.place_day_blocks([block], [window], preserve_order=True)
    assert placed == [] and overflow == [block]
    block['deadline_at'] = '2026-10-14T09:00:00'
    placed, overflow = scheduler_engine.place_day_blocks([block], [window], preserve_order=True)
    assert len(placed) == 1 and overflow == []


def test_rendered_plan_preserves_every_minute_when_morning_deadline_cannot_fit():
    task = PlannerTask(id='a', title='Quiz', est_minutes=60, due_date=DAY,
                       deadline_at=datetime(2026, 10, 14, 9))
    plan = build_plan([task], [DayCapacity(DAY, 180)], today=DAY)
    data = plan_to_schedule_data(plan, availability={'Wed': ['evening']}, now=NOW)
    work = [b for d in data['schedule'] for b in d['blocks'] if not b.get('is_break')]
    assert work == []
    assert sum(row['minutes'] for row in data['deferred']) == sum(s.minutes for s in plan.sessions)
    assert all(row['deadline_at'] == '2026-10-14T09:00:00' for row in data['deferred'])
    restored = snapshot_from_schedule(data, {}, DAY)
    assert restored.tasks[0].deadline_at == task.deadline_at


def test_final_verifier_does_not_trust_recovery_flag_for_a_future_deadline():
    data = {'schedule': [{'date': DAY.isoformat(), 'blocks': [{
        'id': 'a', 'assignment': 'Quiz', 'duration_minutes': 60, 'due_date': DAY.isoformat(),
        'deadline_at': '2026-10-14T09:00:00', 'overdue_recovery': True,
        'start_iso': '2026-10-14T09:00:00', 'end_iso': '2026-10-14T10:00:00',
    }]}]}
    result = check_schedule(data, now=NOW, allow_overdue_recovery=True)
    assert any(v.key == 'after_deadline' for v in result.violations)
    later = check_schedule(data, now=datetime(2026, 10, 14, 9, 30), allow_overdue_recovery=True)
    assert any(v.key == 'overdue_recovery' for v in later.violations)


def test_saved_future_sittings_retain_exact_deadline_during_reconstruction():
    task = PlannerTask(id='a', title='Quiz', est_minutes=60, due_date=DAY,
                       deadline_at=datetime(2026, 10, 14, 9))
    rebuilt = plan_from_sittings([task], {'a': [(DAY, 60)]}, [DayCapacity(DAY, 120)], today=DAY)
    assert rebuilt.sessions[0].deadline_at == task.deadline_at


def test_service_converts_explicit_deadline_before_stage_decomposition():
    service = SchedulingService(StudentContext(timezone_name='America/Los_Angeles'))
    tasks = service.tasks_from([{'id': 'essay', 'title': 'Essay project', 'kind': 'project',
                                'est_minutes': 180, 'due_at': '2026-10-14T01:00:00Z'}])
    assert tasks
    assert all(task.deadline_at == datetime(2026, 10, 13, 18) for task in tasks)
    assert all(task.due_date == date(2026, 10, 13) for task in tasks)


def test_planner_uses_previous_day_when_due_morning_has_only_evening_availability():
    service = SchedulingService(StudentContext(availability={'Tue': ['evening'], 'Wed': ['evening']}),
                                decompose_stages=False)
    now = datetime(2026, 10, 13, 8)
    plan = service.plan([{'id': 'a', 'title': 'Quiz', 'due_at': '2026-10-14T09:00:00',
                          'est_minutes': 60}], now=now, horizon_days=2)
    assert plan.sessions
    assert all(s.day == date(2026, 10, 13) for s in plan.sessions)
    data = service.to_schedule_data(plan, now=now)
    assert service.verify(data, now=now).feasible


def test_autopilot_detects_time_only_deadline_change_and_updates_saved_task():
    from dataclasses import replace
    from intelliplan.intelligence.autopilot import detect_due_changes, apply_due_changes
    old = PlannerTask(id='a', title='Quiz', due_date=DAY, deadline_at=datetime(2026, 10, 14, 18))
    live = replace(old, deadline_at=datetime(2026, 10, 14, 9))
    changes = detect_due_changes(iter([old]), iter([live]))
    assert len(changes) == 1
    updated = apply_due_changes([old], changes, DAY)
    assert updated[0].deadline_at == live.deadline_at


def test_assignment_rows_preserve_explicit_source_and_custom_deadline(monkeypatch):
    import App
    monkeypatch.setattr(App, '_sized_estimate', lambda *args: (60, 0, []))
    rows = App._planner_task_rows(
        [{'title': 'School quiz', 'due_date': '2026-10-14', 'due_at': '2026-10-14T16:00:00Z'}],
        [{'title': 'Practice', 'due_date': '2026-10-14', 'due_at': '2026-10-14T09:00:00',
          'deadline_at': '2026-10-14T09:00:00'}])
    assert rows[0]['due_at'] == '2026-10-14T16:00:00Z'
    assert rows[1]['due_at'] == '2026-10-14T09:00:00'
    assert rows[1]['deadline_at'] == '2026-10-14T09:00:00'


def test_planner_task_priority_uses_injected_student_local_date(monkeypatch):
    import App
    from intelliplan.services import prioritisation

    received = {}

    def capture_score_rows(assignments, today):
        received['today'] = today
        return {}

    monkeypatch.setattr(App, '_sized_estimate', lambda *args: (60, 0, []))
    monkeypatch.setattr(prioritisation, 'score_rows', capture_score_rows)
    rows = App._planner_task_rows(
        [{'id': 'due-today', 'title': 'Quiz', 'due_date': DAY.isoformat()}],
        [],
        today=DAY,
    )

    assert received['today'] == DAY
    assert rows[0]['title'] == 'Quiz'


def test_shared_morning_capacity_moves_one_assignment_to_previous_day():
    service = SchedulingService(StudentContext(availability={
        'Tue': {'start': '18:00', 'end': '20:00'},
        'Wed': {'start': '08:00', 'end': '12:00'}}), decompose_stages=False)
    now = datetime(2026, 10, 13, 8)
    rows = [{'id': key, 'title': key, 'est_minutes': 45,
             'due_at': '2026-10-14T09:00:00'} for key in ('a', 'b')]
    plan = service.plan(rows, now=now, horizon_days=2)
    data = service.to_schedule_data(plan, now=now)
    assert not data.get('deferred')
    assert {s.task_id for s in plan.sessions} == {'a', 'b'}
    assert any(s.day == now.date() for s in plan.sessions)
    assert service.verify(data, now=now).feasible


def test_autopilot_clears_removed_exact_time():
    from dataclasses import replace
    from intelliplan.intelligence.autopilot import detect_due_changes, apply_due_changes
    old = PlannerTask(id='a', title='Quiz', due_date=DAY, deadline_at=datetime(2026, 10, 14, 9))
    changes = detect_due_changes([old], [replace(old, deadline_at=None)])
    assert len(changes) == 1
    assert apply_due_changes([old], changes, DAY)[0].deadline_at is None


def test_exact_deadline_still_allows_flexible_sittings_in_short_calendar_gaps():
    service = SchedulingService(StudentContext(
        availability={'Wed': {'start': '08:00', 'end': '11:00'}},
        busy_by_date={DAY: [(509, 540), (569, 600), (629, 660)]}), decompose_stages=False)
    plan = service.plan([{'id': 'a', 'title': 'Quiz', 'est_minutes': 60,
                          'due_at': '2026-10-14T11:00:00'}], now=NOW, horizon_days=1)
    data = service.to_schedule_data(plan, now=NOW)
    assert not data.get('deferred')
    assert sum(b['duration_minutes'] for d in data['schedule'] for b in d['blocks']
               if not b.get('is_break')) == 60
    assert service.verify(data, now=NOW).feasible


@pytest.mark.parametrize('action', ['skip', 'shorten', 'move'])
def test_overrides_keep_exact_time_in_deferred_work(action):
    from intelliplan.services.next_action import plan_from_schedule_data, tasks_from_plan
    from intelliplan.intelligence.overrides import skip_task, shorten_task, move_task
    from intelliplan.api.next_action import _deferral
    block = {'task_id': 'a', 'assignment': 'Quiz', 'duration_minutes': 60,
             'due_date': DAY.isoformat(), 'deadline_at': '2026-10-14T09:00:00'}
    plan = plan_from_schedule_data({'schedule': [{'date': DAY.isoformat(), 'blocks': [block]}]})
    assert tasks_from_plan(plan)[0].deadline_at == datetime(2026, 10, 14, 9)
    if action == 'skip':
        adjusted, _ = skip_task(plan, task_id='a', day=DAY)
    elif action == 'shorten':
        adjusted, _ = shorten_task(plan, task_id='a', day=DAY, minutes=30)
    else:
        adjusted, _, _ = move_task(plan, task_id='a', from_day=DAY)
    assert adjusted.deferred[0].deadline_at == datetime(2026, 10, 14, 9)
    assert _deferral(block, 30, 'Skipped')['deadline_at'] == block['deadline_at']
