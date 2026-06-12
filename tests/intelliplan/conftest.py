"""Test fixtures for the Command Center package.

A standalone in-memory SQLite + Flask-SQLAlchemy harness so tests run
without booting the App.py monolith. We define a tiny stand-in for
``ManualTask`` matching the production schema for the columns the
repository reads — this keeps the tests fast (no Stripe/genai imports)
and isolated from App.py mutations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy


@pytest.fixture()
def app() -> Flask:
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    return app


@pytest.fixture()
def db(app: Flask) -> SQLAlchemy:
    db = SQLAlchemy(app)
    return db


@pytest.fixture()
def models(app: Flask, db: SQLAlchemy) -> dict[str, Any]:
    """Define User + ManualTask stand-ins, plus the Command Center models.

    The User model is the minimal shape needed to satisfy the
    ``users.id`` foreign key referenced by every Command Center table.
    """

    class User(db.Model):
        __tablename__ = "users"
        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(255), unique=True, nullable=False)

    class ManualTask(db.Model):
        __tablename__ = "manual_tasks"
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
        title = db.Column(db.String(512), nullable=False)
        due_date = db.Column(db.String(32), default="")
        course = db.Column(db.String(256), default="Personal")
        estimated_time = db.Column(db.Integer, default=60)
        done = db.Column(db.Boolean, default=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

    from intelliplan.models import command_center as cc_models

    BriefingCache, HealthSnapshot, StudentSignal = cc_models.register(db)

    with app.app_context():
        db.create_all()

    return {
        "User": User,
        "ManualTask": ManualTask,
        "BriefingCache": BriefingCache,
        "HealthSnapshot": HealthSnapshot,
        "StudentSignal": StudentSignal,
    }
