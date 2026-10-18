"""Native ownership and migrated-data behavior beyond shared tutor contracts."""
import mongomock
import pytest
from flask import Flask

from adaptive_tutor import store
from intelliplan.persistence.codec import pack_document
from intelliplan.persistence.mongo import MongoStore


@pytest.fixture
def native_tutor(monkeypatch):
    client = mongomock.MongoClient()
    backend = MongoStore('mongodb://test', 'tutor', client_factory=lambda *a, **k: client)
    monkeypatch.setattr(backend, 'transaction', lambda work: work(backend.database, None))
    app = Flask(__name__)
    app.secret_key = 'native-tutor-test'
    app.extensions['mongo_store'] = backend
    monkeypatch.setattr(store, 'current_owner', lambda: (42, None))
    with app.test_request_context('/'):
        yield backend


def test_another_account_cannot_read_or_change_learner_records(native_tutor, monkeypatch):
    profile = store.get_or_create_profile()
    pid = profile['id']
    imported = store.save_memory_import(pid, 'ChatGPT', 'private learner context')
    store.update_mastery(pid, 'Math', 'Fractions', True)
    store.record_mistake(pid, {'description': 'Private mistake'})
    mistake = store.list_mistakes(pid)[0]['id']
    monkeypatch.setattr(store, 'current_owner', lambda: (43, None))
    for operation in (
        lambda: store.update_profile(pid, {'grade_level': 'malicious'}),
        lambda: store.list_mastery(pid),
        lambda: store.update_mastery(pid, 'Math', 'Fractions', False),
        lambda: store.list_memory_imports(pid),
        lambda: store.delete_memory_import(pid, imported),
        lambda: store.list_mistakes(pid),
        lambda: store.resolve_mistake(pid, mistake),
        lambda: store.get_learner_memory(pid),
        lambda: store.save_session_summary(pid, 7, {}),
    ):
        with pytest.raises(LookupError):
            operation()
    assert native_tutor.database.adaptive_memory_import.count_documents({}) == 1


def test_migrated_compressed_text_is_read_and_can_be_replaced(native_tutor):
    pid = store.get_or_create_profile()['id']
    original = 'long exact learner content ' * 1000
    row_id = store.save_memory_import(pid, 'ChatGPT', original)
    row = store.list_memory_imports(pid)[0]
    assert row['id'] == row_id
    assert row['raw_text'] == original
    assert '_ip_encoding' not in row
    native_tutor.database.adaptive_learner_memory.insert_one(pack_document({
        'id': 500, 'profile_id': pid, 'summary': original, 'learner_type': None,
        'confidence': 0.5, 'strengths_json': '[]', 'friction_points_json': '[]',
        'preferred_patterns_json': '[]', 'recommended_strategies_json': '[]',
        'evidence_count': 3, 'source_count': 1, 'detected_modality': None,
        'modality_scores_json': None, 'last_analyzed_at': '2026-10-17T01:02:03.123456'}))
    assert store.get_learner_memory(pid)['summary'] == original
    store.upsert_learner_memory(pid, {'summary': 'new short summary'}, 2, None, None)
    updated = store.get_learner_memory(pid)
    assert updated['summary'] == 'new short summary'
    assert updated['evidence_count'] == 4


def test_multiple_anonymous_session_summaries_are_retained(native_tutor):
    pid = store.get_or_create_profile()['id']
    store.save_session_summary(pid, None, {'summaryText': 'First'})
    store.save_session_summary(pid, None, {'summaryText': 'Second'})
    assert len(store.list_session_summaries(pid)) == 2


def test_zero_limit_never_exposes_an_unbounded_cursor(native_tutor):
    pid = store.get_or_create_profile()['id']
    store.save_memory_import(pid, 'ChatGPT', 'context')
    assert store.list_memory_imports(pid, limit=0) == []
    with pytest.raises(ValueError):
        store.list_memory_imports(pid, limit=-1)


def test_guests_never_share_the_same_profile(native_tutor, monkeypatch):
    monkeypatch.setattr(store, 'current_owner', lambda: (None, 'first'))
    first = store.get_or_create_profile()
    monkeypatch.setattr(store, 'current_owner', lambda: (None, 'second'))
    second = store.get_or_create_profile()
    assert first['id'] != second['id']
    with pytest.raises(LookupError):
        store.update_profile(first['id'], {'grade_level': 'no'})
