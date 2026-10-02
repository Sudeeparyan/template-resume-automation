# Start here

Clone or download this folder, add your resume, and talk to your AI app. The local career app
keeps your evidence, job checks, tailored resumes and application history together. You review
the resumes and apply through the links yourself.

## 1. Get your own copy and start the app once

On GitHub choose **Use this template** if available, or **Code → Download ZIP** and unzip it.
Developers can clone the repository. Keep one person's workspace in each copy to make setup
simple. A fresh clone contains no profiles or application history.

Follow [Install the dashboard](README.md#install-the-dashboard). On Windows, double-click
**Start Dashboard.cmd**; on macOS use **Start Dashboard.command**. The launcher installs the
app's packages and opens the dashboard. You only need this setup once. You can then use chat
for daily work; the existing dashboard is there whenever you want it.

## 2. Open this folder in your AI app

| Your AI app | Connect this workspace |
|---|---|
| ChatGPT desktop with local project / Codex access | Add this folder to a local project and make it the primary folder. Its AGENTS.md and shared skills guide the work. |
| Claude Code | Open this folder. CLAUDE.md loads the same instructions and .claude/skills/ points to the shared skills. |
| Claude desktop / Cowork | Give the task access to this folder and say “Follow AGENTS.md.” Check whether its environment can run the local app; folder access alone does not prove that. |
| Kimi Code | Start in this folder and say “Follow AGENTS.md.” Its project skills use .agents/skills/. |

The assistant first runs `career doctor`, a small readiness check. An installed app with zero
profiles is ready for **Set me up**. Missing packages or tools produce a specific next step.
You do not need to memorize commands.

After setup, open **Settings → AI provider** and choose **Auto**. The app's job search, profile
and resume AI then tries the signed-in Kimi Code, Codex and Claude Code runtimes on this computer.
When one reports a usage limit, the app rests it and tries the next available plan. Sign in to
each runtime separately; an installed executable alone does not prove its account can answer.
The **Paid AI calls a day** setting controls Azure/API calls after the local plans; set it to
**0** if you want only the local plans. The AI app hosting your conversation still has its own
limit. To continue chatting on another plan, open this same folder in Claude or Kimi; the app's
saved profile and job progress stay shared.

The [OpenAI local-project guide](https://learn.chatgpt.com/docs/projects) describes folder
access. [Kimi's skill guide](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/skills.html)
describes project skill discovery. Menus and availability depend on your installed version.

## 3. Add your resume and say “Set me up”

Put a PDF, DOCX, TXT or Markdown resume in **me/**, or attach it to the chat and ask the
assistant to save it there. Add notes about projects, courses or preferences if useful.
Paste this first message:

```text
Follow AGENTS.md. Set me up using my resume and notes in me/.
Use the installed application and tell me what is missing before searching.
Ask only for facts my documents do not provide. Keep my evidence and job history in the app.
```

The assistant asks for the country (Ireland, US or both), your permission to work, target roles
and morning preferences. It imports the sources and builds your profile through the same
services as the dashboard. Review the resulting facts and any open questions. Missing work
authorization stays unknown; jobs that depend on it wait for your answer.

Setup is complete when the build succeeds and the profile is ready. If sign-in or PDF tooling
is missing, fix that and resume the same profile. Your chat account's sign-in does not
automatically sign in the separate AI CLI the app uses.

## 4. Ask for jobs

> Give me 5 jobs with resumes.

The assistant runs the app's search for your profile, then reads its saved results. Each ready
item has a role, company, checked apply link, fit evidence, honest gaps, permit wording when
available, and the current tailored resume. If only three are ready, it reports three and
explains what is pending. Searches can take up to 90 minutes; a running search is not a finished
list. Always read your resume before submitting it.

| Say | What happens |
|---|---|
| “Make a resume for this job” + a link | Check the posting and tailor from your saved evidence |
| “Today's jobs” | Refresh and read your saved morning list |
| “I applied to Example Co today” | Record the application against the exact saved job |
| “Not interested in this job” | Remove it through the app so it is not suggested again |
| “I finished this course” | Save your words as evidence and rebuild your profile |
| “Prepare me for my interview at Example Co” | Use the posting and your actual experience |

Your app outputs live in `career-dashboard/profiles/<id>/`; the latest personal list is
`career-dashboard/profiles/<id>/daily-job-search/MORNING-JOBS.md`. The dashboard links your
resumes. **my-jobs/** is only for the separate AI-only fallback.

## 5. Get your morning message

Say **“Set up my morning jobs: 5 jobs by 07:30 in my timezone.”** The assistant confirms the
timezone and records the exact profile ID. There are two parts:

1. **Prepare the jobs:** on Windows enable **Settings → This profile → Morning jobs** in the
   dashboard. This runs the overnight search and writes the morning list. Keep the PC available.
   Other systems can use an OS or AI-host scheduler with the commands in
   [AUTOPILOT.md](daily-job-search/AUTOPILOT.md).
2. **Bring the result into chat:** create a daily scheduled task in your AI host, using this
   original local folder and the prompt below. Replace `<id>` with your saved profile ID.

```text
Morning jobs for profile <id>. Use this original local project folder. Follow AGENTS.md and
the morning-jobs skill. Refresh and read only this profile's saved morning list. Bring me
up to 5 checked jobs with apply links and their current tailored resumes. State the actual
ready count, pending work and blockers. Do not apply, contact anyone or change my profile.
```

- **ChatGPT desktop / Codex:** use **Scheduled** (older versions: Automations) and choose local
  execution in this project. An isolated Git worktree lacks your ignored profile and resumes.
  Keep the computer on and the app running. Web-only tasks cannot run this local folder.
  See [OpenAI's scheduled-task guide](https://learn.chatgpt.com/docs/automations).
- **Claude Cowork:** use **Scheduled** with this folder, then test that the task can run the app
  command. See [Claude's scheduling guide](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork).
- **Kimi Code:** use a scheduler only if your installed host offers it. Otherwise the local app
  or OS scheduler can prepare the files and you can ask Kimi for “today's jobs.” Do not assume
  that saying “every morning” creates a persistent task.

Run the scheduled prompt once manually, then check that the task is saved and its first result
arrives. The local app writes files; it does not by itself send a ChatGPT, Claude or Kimi message.

## Optional: Gmail and Drive

Neither is required to find jobs or make local resumes. Connect them in your AI host only if
you want to use them, and check which tools are available in scheduled runs too.

- **Drive:** ask the assistant to download a selected resume into `me/`, or upload a finished
  resume to your chosen folder. The app keeps its local evidence and output; cloud sync is not
  automatic.
- **Gmail:** ask to check application replies, or opt in to emailing the morning list to your
  own address in `me/about-me.md`. No employer is contacted. A connected Gmail tool in chat is
  separate from the dashboard's optional mail configuration.

Private folders are excluded from normal Git commits. AI providers receive the material needed
for the actions you request; cloud uploads and email happen only when requested. Do not share a
ZIP of a used workspace as though it were an empty template. Use Git's clean files or the
[template reset guide](docs/RESET-TEMPLATE.md).

## If something is missing

| Problem | Next step |
|---|---|
| The app cannot run in this chat | Open the local folder in a host with command access, or follow the install guide |
| No AI provider is ready | Sign in to a supported AI CLI, then retry the same profile build |
| No tailored PDF is ready | Read the reported PDF/tooling or validation issue; do not submit a placeholder |
| Fewer than five checked jobs | Use the actual ready list; pending checks and weak matches do not fill the quota |
| A scheduled chat has no profile | Point it at the original local project, not a new worktree |
| You can only use a cloud sandbox | AI-only mode uses me/ and my-jobs/; it is not connected to the local app or its history |

Developers: see [the architecture, commands and checks](docs/DEVELOPERS.md).
