"""SQLAlchemy models for the Command Center.

Models are exposed through a ``register(db)`` callback so this package
never has to import ``App`` (which would be a circular import).

Typical usage from ``App.py``::

    from intelliplan.models import command_center as cc_models
    BriefingCache, HealthSnapshot, StudentSignal = cc_models.register(db)
"""
