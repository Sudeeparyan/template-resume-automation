"""The shared Irish job market: public postings every profile on this computer can see.

``data/market/market.db`` holds what public sources publish (employer feeds, EURES and job
boards): the posting, where it was read, its advertised pay and the facts a posting states.
It never holds a person's documents, fit, saved jobs or applications; a profile overlays its
own state from its private database when it reads the market (the Tracker).

Modules: ``schema`` (tables and migrations), ``store`` (the repository every caller uses),
``normalize`` (county, kind, level and role family), ``readers`` (EURES), ``salary_estimates``.
"""
