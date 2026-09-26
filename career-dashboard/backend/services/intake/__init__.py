"""Build a new profile's whole workspace from the documents a person uploads.

    extract.py      Word / PDF / text -> ordered, numbered blocks (no AI, nothing dropped)
    build.py        blocks -> AI extraction per section -> audit -> merge -> the file set
    coverage.py     every block is cited, judged narrative, or kept verbatim
    resume_base.py  the evidence-tagged base resume from the new registry
    api.py          the shell routes the onboarding page uses

The person reviews what was found before anything is built; building writes the
profile's own files and then the app seeds its database from them, exactly as it
does for the backup profile.
"""
