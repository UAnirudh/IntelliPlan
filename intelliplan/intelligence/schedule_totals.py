"""Pure workload totals shared by manual plans and calendar reflow."""

WORKLOAD_COLORS = {'light': '#dcfce7', 'moderate': '#fef3c7', 'heavy': '#fee2e2'}


def recompute_day_totals(schedule_data, hours_per_day=2, *, colors=None):
    colors = WORKLOAD_COLORS if colors is None else colors
    total_study = 0
    for day in schedule_data.get('schedule', []) or []:
        blocks = day.get('blocks', []) or []
        study = sum(int(block.get('duration_minutes') or 0) for block in blocks
                    if not block.get('is_break') and not block.get('unplaced'))
        breaks = sum(int(block.get('duration_minutes') or 0) for block in blocks if block.get('is_break'))
        total_study += study
        day.update(study_minutes=study, break_minutes=breaks, total_minutes=study + breaks,
                   total_hours=round((study + breaks) / 60, 1))
        if study >= max(int(hours_per_day * 60 * 0.85), 150):
            level = 'heavy'
        elif study >= max(int(hours_per_day * 60 * 0.55), 90):
            level = 'moderate'
        else:
            level = 'light'
        day.update(workload_level=level, color_theme=colors[level])
    schedule_data['total_study_time'] = f'{total_study // 60} hours {total_study % 60} minutes'
    return schedule_data
