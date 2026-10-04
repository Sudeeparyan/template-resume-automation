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
- **Tracker** (the Tracker tab): every Irish posting the app has read, one row per role, with the
  posting's own permit sentence (quoted), DETE's permit numbers for the employer, advertised pay
  and the person's own saved/applied marks. Filters, CSV/Excel export and saved-filter alerts;
  "Save to my jobs" re-reads the posting and runs the same gates. It is the app's own graduate
  tracker built from primary sources (askmanavi is never read). `career market status` shows how
  many postings it holds and when it was last refreshed; `career market refresh` re-reads the free
  public sources for this computer's profiles without AI (it changes no one's saved jobs).
- Save a lead found by hand with `career add --url <link> --profile <profile-id>` (the link is
  first resolved to the employer's own posting), `career add --file job.json --profile <profile-id>`, or the Assistant's
  `save_posting`, never by editing the database.

## What the feeds read (`career-dashboard/backend/services/job_sources.py`)

| Source | How |
|---|---|
| Tracked companies | the profile's `data/config/portals.yml`: Greenhouse, Lever (US or EU), Ashby, Workday, SmartRecruiters, Workable, Recruitee, Personio or Teamtailor boards |
| Employer directory | `career-dashboard/backend/countries/ie/employers.yml`: 67 employers with public boards and Irish openings (checked 2026-09-26) |
| Permit employers (registry) | `career-dashboard/backend/countries/ie/employer-registry.csv`: careers boards of employers in DETE's permit statistics, each matched to the DETE legal name and checked for an Irish posting when the registry was built (`backend/scripts/build_employer_registry.py`) |
| EURES / JobsIreland | the EURES portal's public search (Ireland, the role in the job title, newest first). Irish vacancies there come from JobsIreland, where an employer must advertise before applying for a General Employment Permit; each lead links to its JobsIreland page |
| gradireland | sitemap, then each job page's schema.org JobPosting (graduate roles, internships) |
| jobs.ie | search pages, then each posting (many recruitment agencies; label them) |
| Careerjet, Jooble (optional) | only with the person's own key (Settings, Job sources). Their results are leads: the app looks each one up on the employer's own board and never opens the aggregator's tracking link itself; unmatched leads appear in the Tracker with the aggregator's attribution, for the person to open |

The fetcher identifies itself, obeys robots.txt and its Crawl-delay (10 seconds on europa.eu),
paces requests, waits out a site's Retry-After and leaves a failing site alone for the rest of a
pass. **LinkedIn, Indeed, IrishJobs.ie and Glassdoor refuse automated reading**: reach them only
through web search, and give the employer's direct link whenever the same role is on its careers
page. Everything read is also kept in the shared public market store
(`career-dashboard/data/market/market.db`): advertised salaries there give market estimates, and
the Tracker lists it. How each source may be used (keys, attribution, personal-use-only boards,
request budgets) is in `career-dashboard/backend/market/policy.py`.

## AI-only mode: the same sources by hand

1. **Employer feeds.** For employers in `countries/ie/employers.yml` whose sectors fit the person,
   read the public job feed and keep postings located in Ireland:
   - Greenhouse: `https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`
   - Lever: `https://api.lever.co/v0/postings/<token>?mode=json` (EU boards: `api.eu.lever.co`)
   - Ashby: `https://api.ashbyhq.com/posting-api/job-board/<token>`
   - SmartRecruiters: `https://api.smartrecruiters.com/v1/companies/<token>/postings?country=ie`
   - Workday: open `https://<host>/<site>` and search the role.
   - Workable: `https://apply.workable.com/api/v1/widget/accounts/<account>?details=true`
   - Recruitee: `https://<company>.recruitee.com/api/offers/` · Teamtailor: `https://<company>.teamtailor.com/jobs.rss`
   - Personio: `https://<company>.jobs.personio.de/xml` (only where its robots.txt allows)
   - The registry (`countries/ie/employer-registry.csv`) lists more boards of DETE permit employers.
2. **Boards:** jobsireland.ie and EURES (where employers advertise before a General Employment
   Permit), gradireland.com (graduates), jobs.ie, irishjobs.ie, publicjobs.ie (public sector),
   LinkedIn and Indeed Ireland, and job-board connectors your app has.
3. **Focused web searches**, one role at a time:
   - `"<role>" Ireland (site:myworkdayjobs.com OR site:smartrecruiters.com OR site:greenhouse.io OR site:lever.co OR site:ashbyhq.com)`
   - `"<role>" site:irishjobs.ie` · `"<role>" site:jobs.ie` · `"<role>" site:gradireland.com`
   - `"<role>" Ireland site:linkedin.com/jobs/view` · `"<role>" site:publicjobs.ie`
   - Graduates: `"graduate programme" <next year> Ireland "<role>"`

## Confirming a lead (both modes)

1. Read the posting where it is published (the applicant-tracking page or the page's schema.org
   JobPosting), not a copy on an aggregator. A pasted aggregator link counts only once it leads to
   the employer's own posting (one applicant-tracking link on the page, or a redirect to it);
   otherwise ask the person for the employer's link.
2. The location must be in the Republic of Ireland or explicitly Ireland-remote. Belfast, Derry
   and the rest of Northern Ireland are the UK.
3. Quote any work-permit sentence exactly. The Ireland rules
   (`career-dashboard/backend/countries/ie/sponsorship.yml`) exclude postings that refuse a
   permit or require Irish/EU/EEA citizenship, Stamp 4 or a security clearance. "Must be eligible
   to work in Ireland" does not exclude someone who already holds a permission to work (for
   example Stamp 1G). Garda vetting is a background check, not a citizenship rule.
   JobsIreland and EURES add the same notice to every advert ("a non-EEA National, unless they
   are exempted, must hold a valid employment permit"): it restates the law, it is not the
   employer refusing a permit, and the app ignores it.
4. In App mode each Irish job shows a **permit-path evidence score** (0-100: DETE permits issued
   to the employer, the posting's own words, pay against the person's threshold, the occupation
   list and a JobsIreland/EURES listing). Report it with its label, "evidence score, not approval
   likelihood". When a posting states no salary, a **market estimate** (from at least five
   advertised salaries at three employers) may stand in, always labelled as an estimate: tell
   the person to confirm the base salary with the recruiter before applying.
5. Irish CVs: A4, Irish/UK spelling (`tailor-resume`).

Never invent a job, submit an application or contact anyone.
