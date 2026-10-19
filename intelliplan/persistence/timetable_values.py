"""Pure timetable record conversion shared by SQL and native repositories."""
from intelliplan.domain.timetable import Meeting, Rotation, parse_clock, parse_int_list, parse_weekdays
from intelliplan.persistence.records import unpack_record
from intelliplan.persistence.json_values import loads_exact


def json_value(raw, fallback):
    try:
        value = loads_exact(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def meeting(raw):
    row = unpack_record(raw)
    return Meeting(id=row['id'], course=row.get('course') or '', period=row.get('period') or '',
        room=row.get('room') or '', teacher=row.get('teacher') or '',
        start_minute=parse_clock(row.get('start_time')), end_minute=parse_clock(row.get('end_time')),
        weekdays=parse_weekdays(row.get('weekdays')), rotation_days=parse_int_list(row.get('rotation_days')),
        color=row.get('color') or '', source=row.get('source') or 'manual')


def settings(raw):
    row = unpack_record(raw) if raw else {}
    rotation = Rotation.from_dict({'kind': row.get('rotation_kind') or 'none',
        'length': row.get('cycle_length') or 1, 'anchor_date': row.get('anchor_date'),
        'anchor_day': row.get('anchor_day') or 1,
        'school_weekdays': row.get('school_weekdays', 'Mon,Tue,Wed,Thu,Fri') or '',
        'skip_days': json_value(row.get('skip_days_json'), [])})
    bell = {str(key)[:32]: [str(span[0]), str(span[1])]
        for key, span in json_value(row.get('bell_json'), {}).items()
        if isinstance(span, (list, tuple)) and len(span) >= 2}
    return rotation, bell

