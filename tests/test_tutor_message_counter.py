"""The free-plan tutor counter reads two User columns that must exist.

``_check_and_increment_tutor_limit`` read ``tutor_reset_date`` and
``monthly_tutor_messages`` before the model defined them. Every signed-in
student's tutor message raised AttributeError and came back as "Sorry, I hit
a snag" (production, 2026-10-07).
"""

from datetime import datetime

import pytest

import App
import chatbot_api
from App import User, db


@pytest.fixture
def user_id():
    App.app.config["TESTING"] = True
    with App.app.app_context():
        db.create_all()
        User.query.filter_by(email="counter+student@example.com").delete()
        user = User(email="counter+student@example.com", password_hash="", birth_year=2005)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    yield uid
    with App.app.app_context():
        User.query.filter_by(id=uid).delete()
        db.session.commit()


def _check(uid):
    from flask_login import login_user

    with App.app.test_request_context():
        login_user(db.session.get(User, uid))
        return chatbot_api._check_and_increment_tutor_limit()


def test_the_counter_runs_for_a_signed_in_student(user_id):
    allowed, remaining, limit = _check(user_id)
    assert allowed is True and limit == 50 and remaining == 49


def test_the_counter_resets_on_the_first_of_next_month(user_id):
    _check(user_id)
    with App.app.app_context():
        user = db.session.get(User, user_id)
        assert user.monthly_tutor_messages == 1
        assert user.tutor_reset_date > datetime.utcnow()
        assert user.tutor_reset_date.day == 1


def test_the_limit_is_enforced(user_id):
    with App.app.app_context():
        user = db.session.get(User, user_id)
        user.monthly_tutor_messages = 50
        user.tutor_reset_date = datetime(2999, 1, 1)
        db.session.commit()
    assert _check(user_id) == (False, 0, 50)


def test_a_paying_student_is_not_counted(user_id):
    from datetime import timedelta

    with App.app.app_context():
        user = db.session.get(User, user_id)
        user.paid_until = datetime.utcnow() + timedelta(days=10)
        user.monthly_tutor_messages = 500
        db.session.commit()
    assert _check(user_id) == (True, None, None)
