# Career Workspace

A private job-search workspace for Ireland and the US. It finds real, open jobs that fit you and
makes an honest, tailored resume for each one. You review and submit applications yourself;
the assistant never applies or contacts employers. The GitHub copy starts empty: no person,
no jobs, no keys. Begin with **[START-HERE.md](START-HERE.md)** for the chat-to-app workflow.

There are two ways to use it, and they work together:

| | Just an AI app | AI app + the dashboard |
|---|---|---|
| For | anyone, nothing to install | the strongest results, or developers |
| You need | Claude (desktop app), ChatGPT (Codex app) or Kimi (Kimi Code) | the same, plus Python, Node.js and Tectonic on your computer |
| Jobs come from | the AI's web search and job-board connectors | 67 Irish employers' job feeds, Irish graduate boards, then focused AI searches, overnight |
| Resumes | written and checked by the AI (PDF and Word) | the evidence registry, Resume Studio and a validated PDF at an exact page count |
| Start | fallback when the local app cannot run; separate files and tracker | [install the dashboard](#install-the-dashboard), then **[START-HERE.md](START-HERE.md)** |

Either way you talk to your AI app the same way ("Set me up", "Give me 5 jobs with resumes",
"Today's jobs"). The AI app reads [AGENTS.md](AGENTS.md) and the shared skills in
[.agents/skills/](.agents/skills/). Its first command, `career doctor`, checks whether the app
can run even before you have a profile. For the connected workflow, use the app-backed route;
AI-only output is not automatically imported into the dashboard.

## Install the dashboard

1. Install [Python 3.12](https://www.python.org/downloads/) and
   [Node.js 20 or newer](https://nodejs.org/en/download). It must be 3.12, not the newest Python
   that the download page offers first: the OCR packages need 3.12. On Windows, install Python
   with its `py` launcher. On macOS, make sure `python3.12` is on `PATH`.
2. Install [Tectonic](https://tectonic-typesetting.github.io/book/latest/installation/) for resume
   PDFs. Scanned PDF OCR is included in the Python dependencies. If you already have
   [Poppler](https://poppler.freedesktop.org/) (`pdftoppm`) and
   [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html), the app can use those too.
3. On Windows, double-click **Start Dashboard.cmd**. On macOS, run
   `bash "Start Dashboard.command"` from this folder. The launcher creates a private Python
   environment, installs the pinned backend and frontend packages, reports which optional tools and
   AI providers are ready, builds the web client, then opens the app on `127.0.0.1:8000`.
4. The app's AI steps use an AI app signed in on this computer (Claude Code, Codex or Kimi Code)
   or a key: copy `career-dashboard/.env.example` to `career-dashboard/.env` and fill in one
   provider. Restart the launcher after adding a key or signing in, and check its AI readiness
   report. The `.env` file and each profile folder stay on your computer.
5. Create a profile once: in the dashboard (choose Ireland, US or both, add a DOCX, PDF, TXT, MD or typed
   note to its Sources, then **Build Agent for You**), or tell your AI app "Set me up", which runs
   `career setup` on the files in `me/`. Review flagged facts and work authorization before using
   job actions. Rebuild when you add new sources; job history stays with the profile. If a build
   fails, retry with that profile ID rather than creating another person. A ready profile and
   completed build, not an uploaded file alone, finish setup.

For a read-only diagnosis at any time, run `.\career.cmd doctor` on Windows or `sh career doctor`
elsewhere. It reports missing dependencies and tools without opening resumes, credentials or
starting AI calls. Provider detection does not prove sign-in. Gmail and Drive are optional
AI-host connectors, not prerequisites or automatic connections to the dashboard.

The app serves only this computer. Pass `--port 8001` to use another port, `--no-browser` to keep
it from opening a tab, and `--preflight-only` to install and check tools without starting the
server. Keep the launcher window open while using the app.

The eight tabs are Assistant, Dashboard, Daily Search, Resume Studio, Profile, Assurance, Agents
and Settings. Profile facts are grounded in your sources; uncertain details stay flagged. Job
search and AI calls may use external services when you start those actions. Mail is optional and
requires a configured connector.

## Morning jobs

Switch on **Settings → This profile → Morning jobs** and pick a ready-by time (09:00 by default).
Windows then searches overnight and has a list ready by that time in
`daily-job-search/MORNING-JOBS.md`: each new job with its fit score, the reasons it fits, the apply
link and a tailored resume PDF, plus the saved jobs you still have to apply for and the
applications you made (never suggested again). The list is written every morning even if
something went wrong: the run restarts the app, waits for the internet or for a free AI plan to
reset, and carries on. Only a problem you must fix yourself appears under **Needs you**.

To find jobs now: `daily-job-search\morning-jobs.cmd --jobs 5` (macOS:
`bash daily-job-search/morning-jobs.command --jobs 5`). To have your AI app bring you the list,
see [START-HERE.md](START-HERE.md) and [daily-job-search/AUTOPILOT.md](daily-job-search/AUTOPILOT.md).

The search behind it is the **Overnight hunt** (Daily Search, or tell the Assistant
*overnight hunt*). It reads employer career feeds directly (a verified list of Irish employers plus
your tracked companies), gradireland, jobs.ie and the askmanavi graduate tracker, then runs
focused AI web searches for each role across company job sites, Irish job boards, LinkedIn and
publicjobs.ie. Every job passes the location, work-permit, never-re-apply, legitimacy and
requirement checks. When you remove a job, say why: the next searches skip that company or that
job title. LinkedIn, Indeed and IrishJobs.ie refuse automated reading, so the AI reaches them only
through its web search.

## Privacy

Profiles live under `career-dashboard/profiles/<id>/`, each with its own sources, SQLite database,
search history and outputs. `me/` and `my-jobs/` hold what the AI-app path writes. Git never
publishes any of these, API keys or `backup/`. Before publishing changes, run
`python scripts/scan_release.py`. To reuse an already populated local copy, follow
[Reset to an empty template](docs/RESET-TEMPLATE.md). Resetting private data does not inspect or
erase backups, external chats or Git history; distribute the clean Git tree, not a used folder ZIP.

## For developers

Architecture, the `career` CLI, the API, tests and the release gate are in
[docs/DEVELOPERS.md](docs/DEVELOPERS.md). Run **Check Workspace.cmd** on Windows or
`bash "Check Workspace.command"` on macOS after changes: backend and frontend tests, the
production build, workspace and profile validators, the privacy scan and a first-run smoke test.
