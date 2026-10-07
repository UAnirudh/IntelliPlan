"""Premium: a metered Claude tutor, model routing, and linked AI accounts.

Pure rules live here -- prices, plans, routing heuristics, the Claude call
and the provider calls for linked keys. ``premium_glue.py`` at the repo root
owns the database, the routes and the hooks into ``ai_provider``, matching
the other ``*_glue.py`` modules.

How a Premium tutor turn works
------------------------------
1. A cheap, fast model (Groq) reads the question and returns a route: what
   is being asked, how hard it is, whether it is too vague to answer well,
   and which Claude model fits. Without Groq, a keyword heuristic decides.
2. If the question is too vague, the student gets clarifying questions back
   instead of an answer. Nothing expensive has run yet.
3. Otherwise the chosen Claude model answers with the tutor prompt. The
   exact token usage is priced and written to the spend ledger, and the
   student's remaining budget for the month goes down by that amount.

A student who links their own AI account is billed nothing for the calls
that run on their key; those are recorded with ``source="byok"`` and do not
draw on the budget.
"""
