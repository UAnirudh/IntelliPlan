"""Adaptive tutoring layer for Plani.

A port of the adaptive-ai-tutor architecture onto IntelliPlan's Flask stack:
a persistent student model (profile, mastery, mistakes, durable learner
memory, imported context) that shapes every tutor reply, plus modality
routing and interactive artifacts.

Modules:
  ``store``     SQLAlchemy Core tables and CRUD for the student model
  ``prompt``    flattens the student model into a system message
  ``analysis``  LLM passes that keep the model current
  ``modality``  auditory / visual / reading routing
  ``engine``    per-turn orchestration used by ``chatbot_api``
  ``api``       Flask blueprint at ``/api/tutor/adaptive/*``
"""

# Nothing is imported here on purpose: ``api`` -> ``engine`` -> ``store`` all
# import back through this package, so eager imports would be circular. Import
# the blueprint directly:  ``from adaptive_tutor.api import adaptive_tutor_bp``.
