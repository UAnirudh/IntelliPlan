"""Background replanning must fetch each owner's newly assigned work."""
from datetime import datetime
from types import SimpleNamespace

from flask import Flask

from intelliplan.api.adjust import AdjustDeps, create_adjust_blueprint


def test_sweep_fetches_new_work_for_each_explicit_owner_and_respects_opt_out():
    fetched, planned = [], []

    def assignments(uid, gid):
        fetched.append((uid, gid))
        return [{'id': f'new-{uid}', 'title': f'New work {uid}'},
                {'id': 'submitted', 'title': 'Finished essay', 'submission_status': 'awaiting_grade',
                 'requires_action': False, 'awaiting_grade': True}]

    def service(uid, gid, payload):
        def autopilot(data, progress, rows, **kwargs):
            planned.append((uid, rows, kwargs['finished_titles']))
            return None
        return SimpleNamespace(autopilot=autopilot)

    deps = AdjustDeps(
        identity=lambda: (None, None), feature_enabled=lambda uid: True,
        now=lambda: datetime(2026, 11, 17, 9), build_service=service,
        load_plan=lambda uid, gid: ({'schedule': [{'blocks': []}],
                                    'autopilot': {'enabled': uid != 3}}, {}),
        active_owners=lambda: [(1, None), (2, None), (3, None)],
        owner_assignments=assignments,
        build_rows=lambda rows: (_ for _ in ()).throw(AssertionError('caller-scoped mapper must not run')),
        build_background_rows=lambda rows: rows,
    )
    app = Flask(__name__)
    bp = create_adjust_blueprint(deps)
    with app.test_request_context():
        result = bp.sweep_autopilot()
    assert fetched == [(1, None), (2, None)]
    assert planned == [(1, [{'id': 'new-1', 'title': 'New work 1'}], ['Finished essay']),
                       (2, [{'id': 'new-2', 'title': 'New work 2'}], ['Finished essay'])]
    assert result['failed'] == 0


def test_fetch_failure_leaves_plan_untouched_and_other_owners_continue():
    checked = []
    def assignments(uid, gid):
        if uid == 1:
            raise RuntimeError('source temporarily unavailable')
        checked.append(uid)
        return []
    deps = AdjustDeps(
        identity=lambda: (None, None), feature_enabled=lambda uid: True,
        now=lambda: datetime(2026, 11, 17, 9),
        load_plan=lambda uid, gid: ({'schedule': [{'blocks': []}]}, {}),
        build_service=lambda *args: SimpleNamespace(autopilot=lambda *a, **kw: None),
        active_owners=lambda: [(1, None), (2, None)], owner_assignments=assignments,
        save_plan=lambda *args: (_ for _ in ()).throw(AssertionError('must not save')),
    )
    app = Flask(__name__)
    with app.test_request_context():
        result = create_adjust_blueprint(deps).sweep_autopilot()
    assert checked == [2]
    assert result['failed'] == 1
