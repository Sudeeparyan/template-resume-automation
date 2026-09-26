# Backend

All Python lives here: the FastAPI app (`dashboard/`), domain services
(`services/`), CLIs and validators (`scripts/`), agent briefs (`workflows/`) and
the LangChain agent layer (`ai/`). `paths.py` is the directory map.

## Run the server

```sh
cd backend
python3.12 -m venv .venv                      # first time only
source .venv/bin/activate
pip install -r requirements-dev.txt           # first time only
python run.py                                 # http://127.0.0.1:8000
```

`run.py` rebuilds the React client from `../frontend` when its sources changed,
then serves it and the API on loopback. `--port 8001` if 8000 is taken,
`--no-browser` to start without opening one. Ctrl+C stops it.

For frontend work, also run `npm run dev` in `../frontend`; Vite serves the UI
on 5173 and proxies `/api` to this server on 8000.

## Checks

From `career-dashboard/`:

```sh
backend/.venv/bin/python -m pytest -q
backend/.venv/bin/python backend/scripts/validate_workspace.py
backend/.venv/bin/python backend/scripts/workspace.py summary
```

Or run `../Check Workspace.command` for the whole gate. Keys are read from the
process environment, `../.env`, the repository-root `.env`, then `keys.txt`;
see `../.env.example` for the names.
