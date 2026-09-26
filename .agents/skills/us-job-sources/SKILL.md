---
name: us-job-sources
description: >-
  Where and how to find real, open jobs in the United States for the person's target roles,
  including how to read sponsorship wording and an employer's H-1B history from the bundled USCIS
  data. Use with find-jobs for any search in the US.
---

# United States job sources

Read `AGENTS.md` first. Target roles and work authorization come from the person's profile.

## App mode

Use `find-jobs` / `morning-jobs`: the overnight hunt runs focused searches per role and applies
the US rules (`career-dashboard/backend/countries/us/`). Save a lead found by hand with
`career add --file job.json` (it runs the sponsorship gate and never-re-apply check), and check an
employer with `career ws sponsor-check --company "<name>" --file <posting.txt>`.

## AI-only mode

1. **Employer boards** through their public feeds (same URLs as in `ireland-job-sources`:
   Greenhouse, Lever, Ashby, SmartRecruiters), keeping US or US-remote postings.
2. **Boards:** LinkedIn Jobs, Indeed, Dice (technology), ZipRecruiter, Handshake (students),
   USAJOBS (federal roles usually require citizenship), and job-board connectors your app has.
3. **Focused web searches**, one role at a time:
   - `"<role>" (site:myworkdayjobs.com OR site:greenhouse.io OR site:lever.co OR site:ashbyhq.com) "United States"`
   - `"<role>" "visa sponsorship" <city>` when the person needs sponsorship
   - `"new grad" "<role>" <year>` for graduates
4. Hubs and salary bands for context: `career-dashboard/backend/countries/us/regions.yml`.

## The US checks

- **Location:** a US city or explicitly US-remote. "Remote (EMEA/APAC)", Canada or the UK do
  not count.
- **Sponsorship wording** (`countries/us/sponsorship.yml`): a posting that refuses sponsorship
  ("unable to sponsor", "without current or future sponsorship", "must be authorized to work
  without sponsorship") is excluded when the person needs sponsorship now or later. A posting that
  requires US citizenship, permanent residency, a security clearance or ITAR/EAR "US person"
  status is excluded unless they have it. Silence is fine: most postings say nothing. Quote the
  sentence exactly.
- **H-1B history is a ranking signal only**, never a reason to drop a job. Look the employer up in
  `career-dashboard/backend/countries/us/sponsors-uscis.csv` (columns: `employer_key, employer,
  fiscal_year, approvals, denials, state, city`; search `employer_key` in lower case without
  punctuation or "inc"/"llc"). More approvals means a stronger signal. No record is not a refusal.
- **Resumes:** US Letter, US spelling, usually one page early in a career (`tailor-resume`).

Never invent a job, give immigration advice, submit an application or contact anyone.
