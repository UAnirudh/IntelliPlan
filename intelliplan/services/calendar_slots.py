"""Conservative half-hour suggestions from verified student-local busy time."""
import math


def free_time_summary(intervals, *, not_before=7 * 60):
    """Return a real free slot, or an empty result when the day is full.

    Intervals come from complete provider reads in local wall-clock minutes.
    Count each half-hour once even when several calendars overlap.
    """
    start = max(7 * 60, int(math.ceil(not_before / 30)) * 30)
    end = 23 * 60
    busy = []
    for interval in intervals:
        if (not isinstance(interval, (list, tuple)) or len(interval) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in interval)):
            raise ValueError('Calendar availability is incomplete.')
        a, b = interval
        if a < 0 or b > 1440 or b <= a:
            raise ValueError('Calendar availability is incomplete.')
        busy.append((a, b))
    slots = [minute for minute in range(start, end - 29, 30)
             if all(not (minute < b and minute + 30 > a) for a, b in busy)]
    preferred = next((minute for minute in slots if minute >= 18 * 60), slots[0] if slots else None)
    slot = ''
    if preferred is not None:
        hour, minute = divmod(preferred, 60)
        slot = f'{hour % 12 or 12}:{minute:02d} {"PM" if hour >= 12 else "AM"}'
    return {'slot': slot, 'free_hours': len(slots) / 2, 'available': bool(slots)}
