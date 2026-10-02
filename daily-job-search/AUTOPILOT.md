# Morning jobs

A checked list of jobs every morning, with a tailored resume for each one. It is written every day,
even when something went wrong in the night.

## Set it up once

Open the dashboard, go to **Settings → This profile**, switch on **Morning jobs** and pick a
**Ready by** time (09:00 by default). That creates one Windows scheduled task, *Career Morning
Jobs*, for the whole PC. The task:

- starts the **overnight hunt** 7½ hours before the ready-by time (01:30 for 09:00). The hunt runs
  every enabled profile in turn and ends 15 minutes before the ready-by time;
- runs again **at the ready-by time**. If the night's run finished, it just rewrites the list. If the
  PC was off or asleep, it runs a short catch-up search (1½ hours);
- wakes the PC, runs on battery, keeps the PC awake until the list is written, and is restarted if
  it fails.

How many jobs, the fit bar and the helpers are the **Overnight hunt** settings in Daily Search. By
default every saved job goes end to end: the posting is re-read (a closed one gets nothing more),
company research with the hiring-manager view and the fit with the profile, the tailored resume, the
contract-sized PDF, an independent review of that PDF, the study plan and the ready-to-submit check.
Nothing is ever submitted and no one is contacted.

## Where the list is

| File | What it is |
|---|---|
| `daily-job-search/MORNING-JOBS.md` | The index: every profile's new jobs, and where its full list is |
| `career-dashboard/profiles/<id>/daily-job-search/MORNING-JOBS.md` | That person's full list, always the latest |
| `…/daily-job-search/<date>/MORNING-JOBS.md` and `morning-jobs.json` | The same list, kept for each day |
| `…/daily-job-search/history.csv` | Every job the mornings brought, once each |
| `daily-job-search/logs/autopilot-<date>.log` | What the run did, step by step |

Each list shows these sections:
- **Needs you**: shown only when a person must act.
- **New this morning**: fit, why it fits, work-permit note, apply link and tailored resume PDF.
- **Still to apply**: jobs saved in the last 21 days.
- **Waiting for the AI requirement check**: not verified yet.
- **Your applications**: never suggested again.
- **How the search went**: including what the run fixed by itself.

Only jobs passing the same final readiness check as Daily Search count as new ready jobs.
An old posting check, missing or unresolved review of the exact PDF and posting, unsupported claims or
undecided Assurance items leave the job pending. Rewriting the list does not edit or compile
the saved resume.
The independent review must record `pass` with no unresolved issues. Completion alone,
including an older prose-only report, does not pass the readiness check.

## What mends itself

| Problem | What the run does |
|---|---|
| The app is not running, stopped answering or runs old code | Starts or restarts it, on port 8001 or later if another program holds port 8000 |
| The app stopped in the middle of the hunt | The hunt carries on with the jobs already saved and does not redo finished steps |
| A hunt ended with an unexpected problem | Starts it again with the time left, up to 3 times |
| An AI plan reached its usage limit (about every 5 hours) | Rests that plan until it resets and uses the next free one. If all are resting, reads the job boards and waits for the reset. A paid AI is used only when the hunt settings allow it |
| An AI app timed out on a step | Tries that step once more a minute later |
| No AI app is signed in | Still reads the job boards and holds the matches for the AI check. **Needs you**: sign in |
| No internet | Waits for the connection to come back |
| Missing Python packages | Installs them again |
| The dashboard page could not be rebuilt | Serves the previous page; the search and the list still run |
| The PC would go to sleep | Keeps it awake until the list is written |

Only problems a person has to solve appear under **Needs you**. Examples: sign in to an AI app,
confirm your work authorization, add target roles, or free up the port.

## Commands

Windows: `daily-job-search\morning-jobs.cmd`. macOS: `bash daily-job-search/morning-jobs.command`.

| Add | Does |
|---|---|
| nothing | Search (if this morning's search has not run yet), then write the list |
| `--list-only` | Only rewrite the list from what is saved now (under a minute) |
| `--background` | Start the run and return at once |
| `--check` | Health check: what would stop tomorrow's list; no AI, no search |
| `--profile <id>` | Only this profile |
| `--hours 2` | Search for 2 hours now instead of until the ready-by time |
| `--jobs 5` | Find 5 new jobs now, each prepared with a tailored resume (searches up to 1½ hours unless `--hours` says otherwise), even when this morning's hunt has finished |
| `--ready-by 08:00` | A different ready-by time for this run |

Running it twice is safe. A second run while one is working only rewrites the list.

## Using an AI app's scheduler (Claude Cowork, Codex, Kimi)

Point the AI app at this folder and create scheduled tasks with these prompts (the steps for each
app are in [START-HERE.md](../START-HERE.md)). With the Windows task switched on, you only need the
morning prompt. Without it, for example on macOS, also add the evening prompt. Select the
original local folder and the exact profile ID. A new Git worktree does not include ignored
candidate profiles. A cloud-only task cannot read this app's local database; that is the
separate AI-only workflow in `.agents/skills/morning-jobs/SKILL.md`.

**Every day at 09:00, bring me my jobs:**

```text
Morning jobs for profile <id>. In this original local folder, follow AGENTS.md and the
morning-jobs skill. Refresh the profile's saved list and bring me up to 5 ready jobs with
checked apply links and current tailored resumes. State the actual ready count and blockers.
Do not apply, contact anyone or change my profile.
```

**Every day at 23:00, start the night search** (only if the Windows task is off):

```text
In this original local folder, run daily-job-search\morning-jobs.cmd --background --profile <id>
(on macOS: bash daily-job-search/morning-jobs.command --background --profile <id>).
It returns at once and searches overnight.
Reply only with the line it prints.
```

## For AI assistants working in this folder

Follow `.agents/skills/morning-jobs/SKILL.md`. For "today's jobs" or a progress question,
read `career ws summary --profile <id>` and refresh that profile's morning list with
`morning-jobs.cmd --list-only --profile <id>`; for "N jobs now", use `--jobs N --profile <id>`.
Read only that profile's MORNING-JOBS.md and dated JSON. Count current checked jobs with
current tailored PDFs, report pending checks separately, and use the saved dashboard URL. Never
start an application, never send outreach and never edit a profile from this workflow. The rules
in `AGENTS.md` apply.
