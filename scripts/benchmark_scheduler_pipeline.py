"""Local, provider-free scheduler timing; optionally compare a Git revision.

Run from the repository root with ``python scripts/benchmark_scheduler_pipeline.py``.
``--ref HEAD`` loads only scheduler modules from that revision into a temporary
directory; it never changes the checkout. Timings are machine-specific.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
from datetime import date, datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref')
    parser.add_argument('--repeats', type=int, default=7)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='intelliplan-benchmark-') as temporary:
        if args.ref:
            for name in ('scheduler_engine', 'intelliplan.intelligence.planner',
                         'intelliplan.intelligence.calendar_sittings', 'intelliplan.services.scheduling'):
                relative = name.replace('.', '/') + '.py'
                source = subprocess.run(['git', 'show', f'{args.ref}:{relative}'], cwd=ROOT,
                                        capture_output=True, check=True).stdout
                path = Path(temporary) / (name.replace('.', '_') + '.py')
                path.write_bytes(source)
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
        measure(max(3, args.repeats), args.ref or 'working-tree')


def measure(repeats, label):
    from intelliplan.intelligence.planner import DayPlan, Plan, PlannerTask, Session
    from intelliplan.intelligence.calendar_sittings import calendar_sittings
    from intelliplan.services.scheduling import SchedulingService, StudentContext
    import scheduler_engine

    day, now = date(2026, 10, 19), datetime(2026, 10, 19, 7)
    availability = {d: {'start': '16:00', 'end': '22:00'}
                    for d in ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')}

    def timed(name, work):
        timings = []
        work()  # imports and warm-up are excluded
        for _ in range(repeats):
            start = perf_counter()
            work()
            timings.append((perf_counter() - start) * 1000)
        print(json.dumps({'revision': label, 'case': name, 'repeats': repeats,
                          'median_ms': round(statistics.median(timings), 3)}))

    sessions = tuple(Session(task_id=str(i), title=str(i), course='Math', kind='homework',
                             day=day, minutes=15, part_index=1, part_total=1,
                             difficulty='medium', priority=50, due_date=day) for i in range(20))
    plan = Plan(days=(DayPlan(day, sessions, 600, 300),), total_minutes=300)
    windows = {day: [scheduler_engine.Window(now.replace(hour=8), now.replace(hour=18))]}
    timed('resize-already-fitting-20-sittings', lambda: calendar_sittings(
        plan, windows, scheduler_engine.StudyDNA()))

    deadlines = [PlannerTask(id=str(i), title=str(i), est_minutes=15,
                             deadline_at=now.replace(hour=21) + timedelta(days=i % 2))
                 for i in range(60)]
    timed('normalize-60-deadlines-two-days', lambda: SchedulingService(
        StudentContext(availability=availability))._deadline_capacities(deadlines, now))

    for count in (20, 60):
        for exact in (False, True):
            rows = [dict(id=str(i), title=f'Reading {i}', est_minutes=(15, 25, 40, 60)[i % 4],
                         due_date=(day + timedelta(days=1 + i % 12)).isoformat(),
                         **({'deadline_at': (now.replace(hour=21) + timedelta(days=1 + i % 12)).isoformat()}
                            if exact else {})) for i in range(count)]

            def pipeline():
                service = SchedulingService(StudentContext(availability=availability), decompose_stages=False)
                result = service.to_schedule_data(service.plan(rows, today=day, now=now), now=now)
                assert service.verify(result, now).feasible
                assert not result['deferred']

            timed(f'plan-and-render-{count}-tasks-' + ('exact-deadlines' if exact else 'date-only'), pipeline)


if __name__ == '__main__':
    main()
