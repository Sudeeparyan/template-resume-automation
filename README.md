# Career Workspace

A private, local career workspace for Ireland and US job searches. Each person has a separate profile
with their own documents, evidence, job history, chats, and resume outputs. The GitHub copy starts
without a person or a job database. Nothing is submitted to an employer or sent as outreach without
your confirmation.

## First run

1. Install [Python 3.12](https://www.python.org/downloads/) and
   [Node.js 20 or newer](https://nodejs.org/en/download). On Windows, install Python with its `py`
   launcher. On macOS, make sure `python3.12` is on `PATH`.
2. Install [Tectonic](https://tectonic-typesetting.github.io/book/latest/installation/) for resume
   PDFs. Scanned PDF OCR is included in the Python dependencies. If you already have
   [Poppler](https://poppler.freedesktop.org/) (`pdftoppm`) and
   [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html), the app can use those too.
3. On Windows, double-click **Start Dashboard.cmd**. On macOS, run
   `bash "Start Dashboard.command"` from this folder. The launcher creates a private Python
   environment, installs the pinned backend and frontend packages, reports which optional tools and
   AI providers are ready, builds the web client, then opens the app on `127.0.0.1:8000`.
4. Before building the first profile, copy `career-dashboard/.env.example` to
   `career-dashboard/.env` and fill in one provider key, or sign in to a supported Kimi Code,
   Codex, or Claude Code CLI. Restart the launcher after adding a key or signing in, and check its
   AI readiness report. The `.env` file and each profile folder remain only on your computer.
5. Create a profile, choose Ireland, US, or both, and add a DOCX, PDF, TXT, MD, or typed note to its
   Sources. Use **Build Agent for You** to extract an evidence-backed profile and base resume. Review
   flagged facts and work authorization before using job actions. Rebuild when you add new sources;
   prior job history stays with the profile. Settings opens after the first build.

The app serves only the local computer. To use a free port, pass `--port 8001` to the launcher. Pass
`--no-browser` to keep it from opening a tab. Keep the launcher window open while using the app.
Use `--preflight-only` to install dependencies and check tools without starting the server.

## Profiles and privacy

Profiles live under `career-dashboard/profiles/<id>/`. Each contains its own source files, SQLite
database, search history, and generated outputs. The app never commits these folders. API keys stay in
ignored local `.env` files or your environment. The code can be cloned to another computer without
including anyone's profile. Before publishing changes, run `python scripts/scan_release.py` and review
its result.

The eight tabs are Assistant, Dashboard, Daily Search, Resume Studio, Profile, Assurance, Agents, and
Settings. Profile facts are grounded in your sources; uncertain details stay flagged. Job search and
AI calls may use external services when you start those actions. Mail is optional and requires a
configured connector.

## Finding jobs overnight

Daily Search → **Overnight hunt** (or tell the Assistant *overnight hunt*, or run
`daily-job-search\night-hunt.cmd --target 10 --hours 8 --min-fit 75`) keeps searching until it has
saved the number of jobs you asked for at your fit bar, or the time is up. It reads employer career
feeds (a verified list of about 65 employers hiring in Ireland plus your tracked companies), gradireland,
jobs.ie and the askmanavi graduate tracker directly, then runs focused AI web searches per role across
company ATS pages, Irish boards, LinkedIn and publicjobs.ie. Every job passes the same location,
work-permit, never-re-apply, legitimacy and requirement checks; when your free AI plans reach their
usage limits it waits for them to reset and never uses a paid AI unless you allow it. It then prepares
each job (research, tailored resume, study plan, PDF) and writes `HUNT-REPORT.md`. It remembers what it
has already checked, so the next night looks at new postings. Keep the app open while it runs.
LinkedIn, Indeed and IrishJobs.ie refuse automated reading, so they are reached only through the AI's
web search.

## Checks

Run **Check Workspace.cmd** on Windows or `bash "Check Workspace.command"` on macOS. These run the
backend tests, frontend tests and production build, plus the workspace and local-profile validators.
A fresh clone can first run `python scripts/scan_release.py` and the launcher with `--preflight-only`.
