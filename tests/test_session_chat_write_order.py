"""Incremental cursors require room writers to allocate IDs in commit order."""
import uuid

from sqlalchemy import event
from sqlalchemy.dialects import postgresql

import App


def test_post_locks_the_room_before_allocating_a_message_id():
    with App.app.app_context():
        App.db.create_all()
        room = App.LiveSession(title='Write ordering', room_slug=uuid.uuid4().hex)
        App.db.session.add(room)
        App.db.session.commit()
        rid = room.id
        order = []
        def observe_orm(execution):
            statement = execution.statement
            if getattr(statement, '_for_update_arg', None) is not None:
                compiled = str(statement.compile(dialect=postgresql.dialect()))
                assert 'FOR UPDATE' in compiled and 'live_sessions' in compiled
                order.append('room_lock')
        def observe_insert(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith('INSERT INTO SESSION_MESSAGES'):
                order.append('message_insert')
        session_class = App.db.session.session_factory.class_
        event.listen(session_class, 'do_orm_execute', observe_orm)
        event.listen(App.db.engine, 'before_cursor_execute', observe_insert)
        try:
            response = App.app.test_client().post(f'/api/sessions/live/{rid}/messages', json={'body': 'A new message'})
            assert response.status_code == 200
            assert order == ['room_lock', 'message_insert']
            created = response.get_json()['message']['id']
            messages = App.app.test_client().get(f'/api/sessions/live/{rid}/messages?after_id={created - 1}').get_json()
            assert [message['id'] for message in messages['messages']] == [created]
        finally:
            event.remove(session_class, 'do_orm_execute', observe_orm)
            event.remove(App.db.engine, 'before_cursor_execute', observe_insert)
            App.SessionMessage.query.filter_by(context_type='live', context_id=rid).delete()
            App.LiveSession.query.filter_by(id=rid).delete()
            App.db.session.commit()
