# Start here

This folder turns an AI app into your job-search helper. Give it your resume once. Then ask
"give me 5 jobs with resumes", and every morning it brings you new jobs that fit you, with the
apply link and a resume made for each one. It never applies for you and never contacts anyone.

It works with **Claude** (desktop app), **ChatGPT** (the Codex app) and **Kimi** (Kimi Code). You
need one of them, a plan that lets it work with files on your computer, and your resume or CV.

## 1. Get the folder

On the GitHub page, click **Code**, then **Download ZIP**, and unzip it somewhere easy such as
Documents. (If you use Git: `git clone <this repository's link>`.) Your own copy is private: your
resume and job lists never go back to GitHub.

## 2. Open it in your AI app

App menus change over time, so the names below may differ a little in your version.

**Claude (desktop app)**
1. Open **Cowork** and create a **project** from an **existing folder**: pick this folder.
2. In the project's **instructions**, paste: `Follow AGENTS.md in this folder.`
3. Optional: in **Settings → Connectors**, connect **Gmail** (to email yourself the morning list
   or check for replies from employers) and any job-board connectors on offer, such as
   **Indeed**, **Dice** or **ZipRecruiter** (more jobs to choose from).

If you use Claude Code instead, open this folder in it. It reads the instructions by itself and
adds commands such as `/find-jobs`.

**ChatGPT (Codex app)**
1. Install the Codex app and sign in with your ChatGPT account.
2. Add this folder as a **project**. Codex reads `AGENTS.md` and the skills by itself.

**Kimi (Kimi Code)**
1. Install Kimi Code and sign in.
2. Open it in this folder. Kimi reads `AGENTS.md` and the skills by itself.

## 3. Add your resume and say "Set me up"

Put your resume or CV (PDF, Word, text) in the **me** folder inside this folder. Then type:

> **Set me up**

It reads your resume and asks a few short questions: which country (Ireland, the US or both),
your permission to work there, the jobs you want, and when you want your morning list. Answer in
your own words. "Not sure" is a fine answer.

## Everyday requests

| Say | You get |
|---|---|
| **Give me 5 jobs with resumes** | 5 real, open jobs that fit you. Each has why it fits, what is missing, any visa or permit wording, the apply link, and a resume made for that job |
| **Make a resume for this job** + a link | an honest resume for that one job |
| **Today's jobs** | this morning's list |
| **I applied to Example Co** | noted, so it is never suggested again |
| **Not interested in Example Co** | never suggested again |
| **I finished the AWS course** | added to your profile for future resumes |
| **Prepare me for my interview at Example Co** | likely questions and honest answers from your own experience |

Your lists and resumes are saved in the **my-jobs** folder, one folder per day.

## Jobs every morning

Say **"Set up my morning jobs"** and it walks you through it, or do it yourself:

- **Claude:** in your project, open **Scheduled** → **New task**, choose **Daily** and your time,
  pick this folder, and paste the prompt below.
- **ChatGPT (Codex):** **Automations** → **New automation**, choose this project, a daily time and
  the prompt below.
- **Kimi Code:** type "every day at 07:30, run this:" followed by the prompt below.

```text
Morning jobs. In this folder, follow AGENTS.md and the morning-jobs skill: bring me today's
jobs with apply links and tailored resumes. Do not apply, contact anyone or change my profile.
```

Leave your computer on and the AI app open at that time. To get the list by email too, answer
"yes" to "Email me the morning list" in `me/about-me.md` and connect Gmail. It only ever emails
you.

## Want the strongest results? Install the dashboard (optional)

The AI app alone searches the web and job boards and writes each resume itself. The dashboard
adds its own job engine on your computer: it reads 67 Irish employers' job feeds and the Irish
graduate boards directly, and searches overnight until it has found enough jobs that pass every
check. It keeps an evidence record for each fact about you and makes and validates PDF resumes
to an exact page count. Once it is installed, your AI app uses it automatically. See
[README.md](README.md) (about 15 minutes, once).

## Good to know

- **Your data stays with you.** Everything in `me/` and `my-jobs/` stays on your computer.
- **Nothing is invented.** Resumes use only what your documents and answers say. If a job needs
  something you do not have, it is listed as a gap, never written into the resume.
- **Nothing is sent.** You read each resume and apply yourself through the link.
- **Check before you send.** Read every resume once; you are the final reviewer.

| Problem | What to do |
|---|---|
| "I need to know your permission to work" | Answer it once; jobs that depend on it wait until then |
| Fewer jobs than you asked for | The list only shows jobs that pass every check. Ask for more titles, more places, or a lower fit bar |
| You got a Word file but no PDF | Open it in Word or Google Docs and save or download it as PDF |
| The morning task did not run | The computer was asleep or the app was closed. Say "today's jobs" |
| A link says the job is closed | Say "not interested in <company>"; it will not come back |
