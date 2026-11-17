"""Bind long-lived Flask-Login remember cookies to the credential epoch.

Business IDs and ordinary session user IDs stay numeric. Only the signed
remember-cookie payload carries the extra issuance epoch.
"""
from flask import current_app, session
from flask_login import LoginManager, user_logged_in
from flask_login.signals import user_loaded_from_cookie
from flask_login.utils import decode_cookie


class EpochLoginManager(LoginManager):
    def init_app(self, app, add_context_processor=True):
        super().init_app(app, add_context_processor=add_context_processor)
        user_logged_in.connect(self._stamp_epoch, sender=app, weak=False)

    def _stamp_epoch(self, sender, user):
        session['session_epoch'] = int(getattr(user, 'session_epoch', 0) or 0)

    def _set_cookie(self, response):
        user_id = session.get('_user_id')
        epoch = session.get('session_epoch')
        if user_id is None or epoch is None:
            return
        # Let Flask-Login retain its configured signing, expiry and cookie
        # attributes. Restore the ordinary business ID before session saving.
        session['_user_id'] = f'epoch.v1:{user_id}:{int(epoch)}'
        try:
            super()._set_cookie(response)
        finally:
            session['_user_id'] = user_id

    def _load_user_from_remember_cookie(self, cookie):
        value = decode_cookie(cookie)
        if value is None:
            return None
        if value.startswith('epoch.v1:'):
            parts = value.split(':')
            if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
                return self._reject_remember_cookie()
            user_id, epoch = parts[1], int(parts[2])
        elif value.isdigit():
            # Existing remember cookies predate epoch binding. Retain them
            # only until the account first changes its credential epoch.
            user_id, epoch = value, 0
        else:
            return self._reject_remember_cookie()
        user = self._user_callback(user_id) if self._user_callback else None
        if user is None or int(getattr(user, 'session_epoch', 0) or 0) != epoch:
            return self._reject_remember_cookie()
        session['_user_id'] = user_id
        session['_fresh'] = False
        session['session_epoch'] = epoch
        user_loaded_from_cookie.send(current_app._get_current_object(), user=user)
        return user

    @staticmethod
    def _reject_remember_cookie():
        session.pop('_user_id', None)
        session.pop('_fresh', None)
        session.pop('session_epoch', None)
        session['_remember'] = 'clear'
        return None
