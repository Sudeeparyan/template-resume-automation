---
name: ireland-job-sources
description: >-
  Where and how to find real, open jobs in the Republic of Ireland for the person's target roles:
  the app's no-AI feeds and overnight hunt, public employer job feeds, Irish job boards and focused
  web searches, and how to confirm each lead on the posting's own page. Use with find-jobs for any
  search in Ireland.
---

# Ireland job sources

Read `AGENTS.md` first. Target roles come from the person's profile; never substitute a fixed
profession.

## App mode: use the app's own engine

- **Find N jobs now:** `find-jobs` (`morning-jobs --jobs N`). **Every morning:** `morning-jobs`.
- The **overnight hunt** (Daily Search → Overnight hunt, or the Assistant's *overnight hunt*)
  loops until the target is met: no-AI feeds first, then focused AI passes per role and site
  group. It waits for free AI plans to reset and never uses a paid AI unless allowed. Every lead
  passes the market, work-permit, never-re-apply, legitimacy and requirement (fit) checks.
- **One pass without AI:** Daily Search source *Job boards + employer feeds* (preset `feeds`).
- **Search plan:** the Assistant's `search_plan` / `update_search_plan` show and change related
  titles, excluded titles and tracked companies (where to look, never evidence).
- Save a lead found by hand with `career add --file job.json` or the Assistant's `save_posting`,
  never by editing the database.

## What the feeds read (`career-dashboard/backend/services/job_sources.py`)

| Source | How |
|---|---|
| Tracked companies | the profile's `data/config/portals.yml`: Greenhouse, Lever, Ashby, Workday, SmartRecruiters boards |
| Employer directory | `career-dashboard/backend/countries/ie/employers.yml`: 67 employers with public boards and Irish openings (checked 2026-09-26) |
| gradireland | sitemap, then each job page's schema.org JobPosting (graduate roles, internships) |
| jobs.ie | search pages, then each posting (many recruitment agencies; label them) |
| askmanavi graduate tracker | role list, then the employer's own applicant-tracking page |

The fetcher identifies itself, obeys robots.txt and paces requests. **LinkedIn, Indeed,
IrishJobs.ie and Glassdoor refuse automated reading**: reach them only through web search, and
give the employer's direct link whenever the same role is on its careers page.

## AI-only mode: the same sources by hand

1. **Employer feeds.** For employers in `countries/ie/employers.yml` whose sectors fit the person,
   read the public job feed and keep postings located in Ireland:
   - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`
   - Lever: `https://api.lever.co/v0/postings/<token>?mode=json`
   - Ashby: `https://api.ashbyhq.com/posting-api/job-board/<token>`
   - SmartRecruiters: `https://api.smartrecruiters.com/v1/companies/<token>/postings?country=ie`
   - Workday: open `https://<host>/<site>` and search the role.
2. **Boards:** gradireland.com (graduates), jobs.ie, irishjobs.ie, publicjobs.ie (public sector),
   jobsireland.ie, LinkedIn and Indeed Ireland, and job-board connectors your app has.
3. **Focused web searches**, one role at a time:
   - `"<role>" Ireland (site:myworkdayjobs.com OR site:smartrecruiters.com OR site:greenhouse.io OR site:lever.co OR site:ashbyhq.com)`
   - `"<role>" site:irishjobs.ie` · `"<role>" site:jobs.ie` · `"<role>" site:gradireland.com`
   - `"<role>" Ireland site:linkedin.com/jobs/view` · `"<role>" site:publicjobs.ie`
   - Graduates: `"graduate programme" <next year> Ireland "<role>"`

## Confirming a lead (both modes)

1. Read the posting where it is published (the applicant-tracking page or the page's schema.org
   JobPosting), not a copy on an aggregator.
2. The location must be in the Republic of Ireland or explicitly Ireland-remote. Belfast, Derry
   and the rest of Northern Ireland are the UK.
3. Quote any work-permit sentence exactly. The Ireland rules
   (`career-dashboard/backend/countries/ie/sponsorship.yml`) exclude postings that refuse a
   permit or require Irish/EU/EEA citizenship, Stamp 4 or a security clearance. "Must be eligible
   to work in Ireland" does not exclude someone who already holds a permission to work (for
   example Stamp 1G). Garda vetting is a background check, not a citizenship rule.
4. Irish CVs: A4, Irish/UK spelling (`tailor-resume`).

Never invent a job, submit an application or contact anyone.
