---
name: track-applications
description: >-
  Keep the person's application history right so no job is ever suggested twice: record "I
  applied", "not interested", "remove this job", interviews, offers and rejections, and (only when
  they ask) read their email for application replies. Use for "I applied to …", "remove …",
  "any replies?", "what did I apply to?".
---

# Track applications

Read `AGENTS.md` first. Only the person's word or a real email changes a status; silence never
does. The date an email arrived is not the date they applied.

## Recording what they tell you

**App mode.** Find the job's exact id with `career jobs` (match company and title; ask when two
match), then:

```text
career update <id> --status applied --application-date <YYYY-MM-DD>
career update <id> --status interview|offer|rejected|withdrawn [--notes "<their words>"]
```

`applied` needs the date they applied; ask for it when they did not say ("today" is fine). To
remove a job they do not want, use the dashboard (Dashboard → remove, and choose why: not this
company, needs a permit, wrong kind of role, too senior) so the next searches learn from it.

**AI-only mode.** Update `my-jobs/tracker.csv` (header in `my-jobs/README.md`): set `status`,
and for `applied` the `applied_on` date. "Not interested" is `not-interested`, with their reason
in `notes`. Add a line when the job is not there yet. Never delete lines: the tracker is how jobs
are never suggested twice.

## Reading email (only when they ask)

With an email tool connected (for example Gmail), search their inbox for replies about the jobs
in their list: confirmations ("thank you for applying", "application received"), interview
invitations, and rejections. For each match, show the sender, date and one quoted line, and
propose the change. Apply it only after they say yes. A confirmation email can show that they
applied, but its date is the receipt date: ask for the submission date or record it as unknown.
Never reply to, forward, label or delete their email.

## Answering "what did I apply to?"

List applications with company, role, date applied and current status, newest first, then the
count of jobs still to apply for.
