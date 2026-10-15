"""Shared calendar-gap sizing for allocation checks and final rendering."""
from dataclasses import replace
from datetime import datetime, timedelta

import scheduler_engine


def calendar_sittings(plan, windows_by_day, dna, now=None):
    """Resize new sittings to the actual calendar gaps without changing work."""
    from intelliplan.intelligence.planner import MIN_SESSION_MINUTES
    from collections import Counter
    from itertools import groupby
    result = {}
    protected = {s.task_id for s in plan.sessions if s.preserve_sitting}
    resized = set()
    for day in plan.days:
        windows = windows_by_day[day.day]
        chosen = []
        def place(sittings):
            blocks = [{'id': str(i), 'duration_minutes': s.minutes, 'is_break': False,
                       'deadline_at': s.deadline_at.isoformat() if s.deadline_at else None,
                       'overdue_recovery': bool(s.deadline_at and now and s.deadline_at <= now)}
                      for i, s in enumerate(sittings)]
            return scheduler_engine.place_day_blocks(blocks, windows, dna, preserve_order=True)
        for _task_id, group in groupby(day.sessions, key=lambda sitting: sitting.task_id):
            sittings = list(group)
            if sittings[0].task_id not in protected:
                placed, spilled = place(chosen + sittings)
                if not spilled:
                    chosen.extend(sittings)
                    continue
                prefix, _ = place(chosen)
                remaining = sum(s.minutes for s in sittings)
                pieces = []
                for window in windows:
                    previous = [b for b in prefix if
                                window.start <= datetime.fromisoformat(b['start_iso']) < window.end]
                    cursor = window.start
                    if previous:
                        last = previous[-1]
                        gap = 10 if last['duration_minutes'] >= 60 else 5
                        cursor = datetime.fromisoformat(last['end_iso']) + timedelta(minutes=gap)
                    deadline = sittings[0].deadline_at
                    limit = (min(window.end, deadline) if deadline and (now is None or deadline > now)
                             else window.end)
                    room = max(0, int((limit - cursor).total_seconds() // 60))
                    # Fill individual gaps rather than equal-splitting against
                    # the largest one. Preserve a useful final sitting.
                    while room >= MIN_SESSION_MINUTES and remaining >= MIN_SESSION_MINUTES:
                        cap = max(max(s.minutes for s in sittings), dna.block_minutes())
                        minutes = min(room, remaining, cap)
                        tail = remaining - minutes
                        if 0 < tail < MIN_SESSION_MINUTES:
                            minutes -= MIN_SESSION_MINUTES - tail
                        if minutes < MIN_SESSION_MINUTES:
                            break
                        pieces.append(minutes)
                        remaining -= minutes
                        room -= minutes + (10 if minutes >= 60 else 5)
                if remaining:
                    pieces.append(remaining)
                if pieces and all(minutes >= MIN_SESSION_MINUTES for minutes in pieces):
                    first = sittings[0]
                    adjusted = [replace(first, minutes=minutes, reasons=first.reasons + (
                        'Sized to fit your calendar gaps.',)) for minutes in pieces]
                    improved, _ = place(chosen + adjusted)
                    if sum(b['duration_minutes'] for b in improved if not b.get('is_break')) > sum(
                            b['duration_minutes'] for b in placed if not b.get('is_break')):
                        resized.add(first.task_id)
                        chosen.extend(adjusted)
                        continue
            chosen.extend(sittings)
        result[day.day] = chosen
    totals = Counter(s.task_id for sittings in result.values() for s in sittings)
    seen = Counter()
    for day in plan.days:
        updated = []
        for sitting in result[day.day]:
            seen[sitting.task_id] += 1
            updated.append(replace(sitting, part_index=seen[sitting.task_id],
                                   part_total=totals[sitting.task_id])
                           if sitting.task_id in resized else sitting)
        result[day.day] = updated
    return result
