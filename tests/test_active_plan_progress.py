"""Saved plan progress uses the stable ID emitted by the scheduler."""
import active_glue


def test_current_and_legacy_progress_ids_are_restored_without_crossing_blocks():
    blocks = [{'block_id': 'current', 'id': 'legacy'}, {'id': 'old'}, {'block_id': 'untouched'}]
    plan = {'schedule': [{'blocks': blocks}]}
    active_glue._mark_done_blocks(plan, {'current': {'done': True}, 'old': True})
    assert [block.get('done', False) for block in blocks] == [True, True, False]


def test_current_progress_takes_precedence_over_legacy_id():
    plan = {'schedule': [{'blocks': [{'block_id': 'current', 'id': 'legacy'}]}]}
    active_glue._mark_done_blocks(plan, {'current': {'done': False}, 'legacy': True})
    assert not plan['schedule'][0]['blocks'][0].get('done')
