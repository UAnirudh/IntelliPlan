"""Intelligence engine — pure functions, no I/O, no AI.

Every function in this package obeys three rules:

1. Inputs are domain dataclasses or primitives. Outputs are domain
   dataclasses. No ``dict``-shaped contracts.
2. No ``datetime.utcnow``, no ``os.getenv``, no ``db.session``. The
   caller passes ``today`` / ``now`` explicitly so tests stay
   deterministic.
3. No LLM. The Narrator (see :mod:`intelliplan.intelligence.narrator`,
   landing in M3) is the *only* AI in the engine.

This package is the canonical answer to "why is this task #1?" — every
score has a structured rationale, and every rationale component is a
stable key the UI binds to.
"""
