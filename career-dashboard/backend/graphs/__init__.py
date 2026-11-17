"""Durable agent workflows built with LangGraph.

Every LangGraph import lives in this package, so a LangGraph upgrade touches one place:

* ``runtime.py``: the context every node gets besides its state (services, runner, market store);
* ``checkpoint.py``: one SQLite checkpoint store per profile (``data/agents.db``), strict
  deserialization and a 30-day retention;
* ``executor.py``: runs a graph for one agent run (thread ``<graph>:<run id>``), resumes it
  from its last checkpoint after a restart and writes its node events to the run's trace;
* ``ai.py`` and ``policies.py``: AI calls through the runner's cache and gateway, and which
  failures are retried (transient network errors) or waited out (an AI plan's limit);
* ``history.py``: a thread read back checkpoint by checkpoint (the Checkpoints tab and
  ``career trace graph``), redacted unless asked; ``executor.run_graph(rerun_from=...)`` runs a
  research run again from one of its checkpoints as a new run;
* ``studio.py``: LangGraph Studio's graphs (``langgraph.json``), on the synthetic demo profile only;
* the graphs themselves: ``dossier.py`` (company dossier, verified claims only),
  ``research.py`` (company research, hiring-manager view and profile comparison),
  ``job_prep.py`` (a Daily Search job's helpers, one node each) and ``tracker_refresh.py`` (the
  market store re-read for the Tracker; LangGraph's functional API).

Graph state holds only JSON-safe values (ids, hashes, texts and plain dictionaries); services
and connections come from the context and are never checkpointed.
"""
