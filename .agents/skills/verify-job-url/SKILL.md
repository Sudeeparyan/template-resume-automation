---
name: verify-job-url
description: >-
  Check whether job links are still open, expired, broken, redirected to a generic careers page,
  or behind a login. Use before saving or tailoring a role, when refreshing a job list, and for
  "is this job still open?".
---

# Verify a job link

Read `AGENTS.md` first. The checker needs only Python 3 (no packages), so it also works in an
AI app's sandbox:

```text
python career-dashboard/backend/scripts/verify_job_url.py --url "<link>"
python career-dashboard/backend/scripts/verify_job_url.py --file my-jobs/<date>/JOBS.md --delay 8
```

`--file` takes any text or Markdown file and checks every link in it, one request every
`--delay` seconds.

In App mode the same checker is `career verify-url --url "<link>"`. This standalone URL check
does not read a profile and does not accept `--profile`; any subsequent job or resume command
must use the selected profile's exact ID. When you cannot run
commands, open the page yourself and apply the same reading.

Reading the result:

- **Likely active** still needs a look at the page: the title, company, location, the full
  description, any permit or sponsorship wording and a working apply button.
- **Expired**, or a redirect to a generic careers page, is not an open job.
- **Broken** or **login-gated**: open it once in a browser (or find the same role on the
  employer's own careers page) before treating it as closed. LinkedIn and some boards always need
  a login; the employer's page is the better link to give the person.
- Employer and applicant-tracking pages (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) come
  before job-board copies.

An open link says nothing about eligibility: the location, permission-to-work, never-re-apply and
fit checks in `find-jobs` still apply. Never delete application history; change a status with
`track-applications`.
