# Career Workspace app

The FastAPI backend is in `backend/` and the React client is in `frontend/`. Private profiles live in
`profiles/<id>/`; each profile owns its sources, database, search history, chats, and outputs. Public
country packs and shared application code stay outside those folders.

Start from the repository root with `Start Dashboard.cmd` on Windows or
`bash "Start Dashboard.command"` on macOS. The launcher installs dependencies, checks Tectonic, OCR,
and AI access, builds the client, and serves the app on loopback. See the root [setup guide](../README.md).

The active backend suite uses disposable generic profiles. Run it from this folder with
`backend/.venv/Scripts/python.exe -m pytest -q` on Windows or
`backend/.venv/bin/python -m pytest -q` on macOS. `pytest.ini` selects `tests/portable`;
older candidate-specific tests elsewhere under `tests` remain as porting references and
are not part of the release gate. Frontend tests and build run from `frontend/` with
`npm test` and `npm run build`. The root check launcher runs the combined gate.
