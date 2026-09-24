"""ORM model for learned population priors.

One additive table, ``model_priors``: one row per model key, holding the
coefficients every new student's model starts from. It is refitted by a cron
(``/cron/refit-followthrough-prior``) from pooled, title-free plan outcomes,
so the planner gets better for the *next* student every time the current
ones use it.

The payload is numbers only — coefficient means and variances, plus how many
rows and students they came from. Nothing in it identifies anyone.

Registration follows the same ``register(db)`` pattern as
``intelliplan.models.scheduler_decisions``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from time_utils import utcnow


def _utcnow() -> datetime:
    return utcnow()


def register(db: Any) -> type:
    """Define ``ModelPrior`` against ``db``. Idempotent across reloads."""
    registry = getattr(db.Model, "registry", None)
    if registry is not None:
        for mapper in registry.mappers:
            cls = mapper.class_
            if getattr(cls, "__tablename__", "") == "model_priors":
                return cls

    class ModelPrior(db.Model):
        __tablename__ = "model_priors"

        id = db.Column(db.Integer, primary_key=True)
        key = db.Column(db.String(64), nullable=False, unique=True, index=True)
        payload_json = db.Column(db.Text, nullable=False, default="{}")
        sample_size = db.Column(db.Integer, nullable=False, default=0)
        users = db.Column(db.Integer, nullable=False, default=0)
        updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    return ModelPrior
