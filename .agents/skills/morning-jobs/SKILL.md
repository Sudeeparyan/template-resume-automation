---
name: morning-jobs
description: >-
  The person's morning list: new checked jobs with apply links and tailored resumes, jobs still to
  apply for, and anything they must do. Use for "today's jobs", "my morning list", a scheduled
  morning run, and to set up "send me jobs every morning" in Claude, ChatGPT (Codex), Antigravity
  or Kimi.
---

# Morning jobs

Read `AGENTS.md` first.

## When a scheduled task (or the person) asks for this morning's jobs

**App mode.** The app searches overnight when Morning jobs is switched on (Settings → This
profile, or `career morning --profile <id> --on`) or when the AI app's evening task starts it.

Read `career ws summary --profile <id>` each run for the current dashboard state. The morning
list must be refreshed from that same profile's database before answering.

1. Rewrite the list from what is saved (under a minute):
   `daily-job-search\morning-jobs.cmd --list-only --profile <id>` (Windows) or
   `bash daily-job-search/morning-jobs.command --list-only --profile <id>`.
2. Read only `career-dashboard/profiles/<id>/daily-job-search/MORNING-JOBS.md` and the dated
   `morning-jobs.json` for this profile. Verify date, profile, job IDs and returned PDF paths.
   Count only jobs whose shared final verdict is `ready`: the posting check is current, the
   current PDF has evidence and assessment checks, and its independent review matches that
   exact PDF and posting and has a `pass` verdict with no unresolved issues. A completed or
   legacy prose-only review is not a pass. Waiting checks or missing/stale PDFs are pending. Label human review
   when required. Do not read the combined index of other people's jobs for a personal request.
3. When the list has fewer new jobs than they want and there is time, run `find-jobs` for the
   difference instead of padding the list.

**AI-only mode.** Run `find-jobs` for the "Jobs per morning" number in `me/profile.md` (else 5),
at their fit bar, skipping everything already in `my-jobs/tracker.csv`. Then read the tracker for
`suggested` jobs from the last 21 days: those are "Still to apply".

**Reply** (both modes) in this order, short and plain:

```text
Good morning <first name>. <n> new jobs for <date>.

Needs you: <the list's Needs you lines, only when they must act: sign in, confirm a permit date, ...>
Permit dates: <in App mode, the list's "Your permit dates" lines, e.g. Stamp 1G expiry and days left>

1. <Title>, <Company> (<Location>). Fit <score>
   Why: <one line>. Gap: <one line or "none">
   Pay: <the list's Pay line: advertised, a market estimate to confirm, or not stated>
   Permit: <quoted sentence, or "not mentioned">; <the DETE permit record line when the list has one>;
   <the Permit-path evidence line, e.g. "62/100 (Evidence score, not approval likelihood)">
   Apply: <link>
   Resume: <path to the PDF>
...
Still to apply: <n>. Best three: <company, role>; ...
Nothing was submitted and no one was contacted.
```

Copy the Pay, permit and date lines as the list writes them: an estimate is never the vacancy's
pay, a DETE record never promises a permit, the permit-path score counts public evidence and is
never a chance of approval, and none of it is immigration advice.

**Email (only when asked).** When `me/about-me.md` says "Email me the morning list: yes" and an
email tool is connected (for example Gmail), send this same text to the person's own address from
`me/about-me.md`, subject "Your jobs for <date>". If you can only draft, leave a draft. Never
email anyone else and never attach anything but their own resume PDFs.

## When they ask to get this every morning

1. Make sure setup is done (`career-setup`) and `me/about-me.md` has the number of jobs, the time
   and the email choice. Confirm the timezone and exact profile ID. Use the original local
   folder: an isolated Git worktree does not contain ignored profiles, resumes or history.
2. **App mode: switch on the overnight search.** Ask before changing it, then run
   `career morning --profile <id> --on --ready-by <HH:MM> --jobs <n>`, with the ready-by time 15
   minutes before they want the message (a message at 08:00 needs `--ready-by 07:45`). This is the
   dashboard's Settings → Morning jobs switch: one Windows task that searches overnight, prepares
   `<n>` jobs end to end (each with its own tailored resume) and writes the list. Read the JSON:
   success is `morning_jobs.enabled` and `morning_jobs.exists` both true; report `night_start`, and
   tell them to leave the PC on or asleep (not shut down) and plugged in overnight. With
   `supported: false` (macOS, Linux), the job count is still saved; add the evening task in step 5. Run
   `career morning --profile <id>` at any time to show the current settings.
3. **Bring the list into chat.** Give them the task for their app, with the prompt below. Offer to
   create it when your app can create scheduled tasks itself; otherwise walk them through it.

   - **Google Antigravity:** use its built-in scheduled automation (its `automation` skill or
     the `/automation` command): daily at the message time, in their IANA timezone (Ireland:
     `CRON_TZ=Europe/Dublin 0 8 * * *`), with the prompt below and read and write access to this
     folder (`workspace_uris`: this folder's `file:///` address). Offer the test run Antigravity
     suggests: scheduled runs auto-deny every command not recorded then, so the run must use
     exactly `.\career.cmd ws summary --profile <id>` and
     `daily-job-search\morning-jobs.cmd --list-only --profile <id>`. The person switches it on
     in Antigravity's **Automations** dashboard; never enable it for them. The Antigravity app
     must be open at that time.
   - **Claude desktop app (Cowork):** use Scheduled with this folder, the prompt and a daily
     time. Check that the scheduled environment can run `career doctor`; if it cannot, it
     cannot drive the installed app. See the official guide linked in `START-HERE.md`.
   - **ChatGPT desktop / Codex:** create a Scheduled task (older versions: Automations), select
     this local project and local execution, then save the daily time and prompt. Web-only
     tasks cannot operate this PC's folder. See the official guide in `START-HERE.md`.
   - **Kimi Code:** use a scheduling facility only if the installed host exposes and confirms
     one. Otherwise use the app's Windows Morning jobs setting or an OS scheduler running
     the morning command. A natural-language request alone does not create a persistent task.
   - **Windows with the dashboard installed:** step 2's switch (also Settings → This profile →
     **Morning jobs**) searches overnight by itself; the AI app's task only brings the list.

   The computer must be on and the AI app open at that time for tasks that use this folder.
   The app's Windows task writes files; a host scheduled task produces the chat message.
   Gmail/Drive availability in chat does not establish their availability in a scheduled run.
   Test the prompt once and verify the saved task, schedule and result. If no scheduler tool
   is available, provide instructions and say it has not been created.

4. The prompt to schedule (the same in every app; put in their profile ID and job count):

   ```text
   Morning jobs for profile <id>. Use this original local project folder. Follow AGENTS.md and
   the morning-jobs skill. Refresh and read only this profile's saved morning list. Bring me
   up to <n> checked jobs, each with its company, apply link and its own tailored resume. State
   the actual ready count, pending work and blockers. Do not apply, contact anyone or change my
   profile.
   ```

5. Optional, App mode without the Windows task (for example on macOS): add an evening task at
   23:00 with this prompt, so the search runs overnight:

   ```text
   In this original local folder, run bash daily-job-search/morning-jobs.command --background
   --profile <id> (Windows: daily-job-search\morning-jobs.cmd --background --profile <id>)
   and reply only with the line it prints.
   ```

Never start an application, send outreach or edit the profile from this skill. Details of the
app's runner: `daily-job-search/AUTOPILOT.md`.
Replace `<id>`, the count and schedule with the person's saved choices before creating a task.
