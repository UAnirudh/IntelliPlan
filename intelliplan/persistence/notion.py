"""Notion connection storage, independent of the application's SQL bootstrap."""
from datetime import datetime
from types import SimpleNamespace

from flask import current_app

from intelliplan.persistence.codec import unpack_document
from intelliplan.persistence.outlook import SQLOutlook
from time_utils import utcnow


class NotionGrant(SimpleNamespace):
    def __init__(self, raw):
        import secret_box
        values = {key: None for key in ('token', 'refresh_token', 'token_expires_at',
            'connected_at', 'database_id', 'auth_type', 'workspace_id', 'workspace_name',
            'workspace_icon', 'bot_id', 'duplicated_template_id')}
        values.update(unpack_document(raw))
        values.pop('_id', None)
        values.pop('_ip_encoding', None)
        for key in ('token', 'refresh_token'):
            values[key] = secret_box.decrypt(values[key])
        for key in ('token_expires_at', 'connected_at'):
            if isinstance(values[key], str):
                values[key] = datetime.fromisoformat(values[key])
        super().__init__(**values)

    def __repr__(self):
        return f'NotionGrant(id={self.id})'


def same_grant(current, expected):
    return current is not None and expected is not None and all(
        getattr(current, key, None) == getattr(expected, key, None)
        for key in ('id', 'user_id', 'token', 'refresh_token', 'connected_at', 'auth_type', 'workspace_id'))


def connection_values(previous, *, token, oauth=None, workspace_name=None, bot_id=None):
    if not isinstance(token, str) or not token:
        raise ValueError('Notion access token is required')
    values = {'token': token, 'connected_at': utcnow()}
    if oauth is not None:
        template = oauth.get('duplicated_template_id')
        values.update(auth_type='oauth', workspace_id=oauth.get('workspace_id'),
            workspace_name=oauth.get('workspace_name'), workspace_icon=oauth.get('workspace_icon'),
            bot_id=oauth.get('bot_id'), refresh_token=oauth.get('refresh_token') or
                (previous.refresh_token if previous else None), token_expires_at=oauth.get('expires_at'),
            duplicated_template_id=template or (previous.duplicated_template_id if previous else None))
        if template and not (previous.database_id if previous else None):
            values['database_id'] = template
    else:
        # A manually pasted token must not later refresh using an older OAuth grant.
        values.update(auth_type='manual', workspace_name=workspace_name, bot_id=bot_id,
            refresh_token=None, token_expires_at=None)
    return values


def notion_accounts():
    injected = current_app.extensions.get('notion_repository')
    if injected is not None:
        return injected
    store = current_app.extensions.get('mongo_store')
    if store is not None:
        from intelliplan.persistence.mongo_notion import MongoNotion
        return MongoNotion(store)
    from App import NotionIntegration, User, db
    return SQLNotion(db, NotionIntegration, User)


class SQLNotion(SQLOutlook):
    def _snapshot(self, row):
        return NotionGrant({column.name: getattr(row, column.name)
            for column in self.model.__table__.columns}) if row is not None else None

    def connect(self, user_id, **parameters):
        try:
            self._lock(user_id)
            row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().first()
            values = connection_values(self._snapshot(row), **parameters)
            if row is None:
                row = self.model(user_id=int(user_id))
                self.db.session.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            self.db.session.commit()
            return self._snapshot(row)
        except Exception:
            self.db.session.rollback()
            raise

    def _change(self, user_id, values, expected=None):
        try:
            self._lock(user_id)
            row = self.model.query.filter_by(user_id=int(user_id)).populate_existing().first()
            if row is None or (expected is not None and not same_grant(row, expected)):
                self.db.session.rollback()
                return False
            for key, value in values.items():
                setattr(row, key, value)
            self.db.session.commit()
            return True
        except Exception:
            self.db.session.rollback()
            raise

    def refresh(self, user_id, expected, fresh):
        values = {'token': fresh['access_token'], 'refresh_token': fresh.get('refresh_token') or expected.refresh_token,
            'token_expires_at': fresh.get('expires_at')}
        return self._change(user_id, values, expected)

    def select_database(self, user_id, database_id, *, expected=None):
        if not isinstance(database_id, str) or not database_id:
            raise ValueError('Notion database is required')
        return self._change(user_id, {'database_id': database_id}, expected)
