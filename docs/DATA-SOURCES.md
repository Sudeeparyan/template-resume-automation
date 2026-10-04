# Public Irish market data

Public country data is shared across profiles. Candidate evidence and application history remain in each
person's private workspace.

## Employment permits issued to companies (DETE)

The source is the Department of Enterprise, Tourism and Employment's yearly workbook "Employment permits
issued to companies", linked from each year's statistics page
(`https://enterprise.gov.ie/en/publications/employment-permit-statistics-<year>.html`). The importer:

- finds the one companies workbook on each year's page and downloads it from government hosts only;
- reads the XLSX with Python's standard library and checks, before anything is filtered, that each row's
  months add up to its Grand Total and that all rows add up to DETE's own Total row, month by month;
- publishes only employers it can identify as organisations: names with a company legal form (Limited, DAC,
  PLC, UC, LLP, ...) or an institutional word (university, hospital, council, ...), plus the firms listed
  under `organisations` in `employer-aliases.yml`. Everything else (a sole trader, a household employing a
  carer, a name that could be a person) is left out. The metadata counts the rows and permits left out but
  never names them;
- writes `career-dashboard/backend/countries/ie/sponsors-dete.csv` and `sponsors-dete.meta.yml`.

The CSV has one row per legal employer and year: `employer, year, permits, monthly_permits`, where
`monthly_permits` lists only the months with permits as `MM:count` pairs (`04:1;05:2`). The months each
year's workbook covered are listed once in the metadata, with each workbook's URL and SHA-256, the CSV's
SHA-256, DETE's totals and the counts left out. The app derives matching keys from the names itself.

DETE updates the current year's workbook each month. Refresh before a release, from the repository root:

```text
career-dashboard\backend\.venv\Scripts\python.exe career-dashboard\backend\scripts\import_dete_permits.py --years 2022-2026
```

The command prints each year's totals; it refuses a workbook whose totals do not reconcile and then leaves
the bundled files unchanged. Review the diff and run `python scripts/scan_release.py`, which re-checks the
hash, the counts and the privacy filter. The local index (`career-dashboard/data/sponsors/dete.db`, ignored)
rebuilds itself when the CSV, its metadata, the aliases or the matching code change.

How the app uses it:

- A posting that is silent about permits ranks as tier B when its employer had at least
  `tier_b_min_permits` (`countries/ie/sponsorship.yml`) permits issued in the last 24 complete months.
  History only ranks: a missing record never excludes a job, and a posting's own words always decide.
- A posting's employer name matches a DETE legal name exactly (legal form and a trailing "Ireland" or
  "Europe" removed), then through a curated alias, then as a guarded prefix: the brand followed only by
  corporate words ("Stripe" matches "Stripe Payments Europe Limited"; "Citi" never matches "Citi Bus
  Limited"). More than five candidate legal entities is ambiguous and nothing is attributed. The matched
  legal name is always shown next to the numbers.
- To add an alias, list the brand under `aliases` with the legal names exactly as DETE writes them, and add
  a `sources` link to the employer's own legal or group disclosure that shows the relationship.

The snapshot retrieved on 4 October 2026 covers 2022 to 2026 (2026 is January to September): 33,393 rows.
Past permits describe the employer, not this vacancy or anyone's permit outcome. Not immigration advice.

## EURES and JobsIreland vacancies

EURES is the European Commission's job mobility portal. Its Irish vacancies come from JobsIreland,
the Department of Social Protection's public employment service (EURES connection point 18). An
employer who intends to apply for a General Employment Permit must advertise the vacancy on
JobsIreland and EURES first (the Labour Market Needs Test), so these vacancies are a strong
source for the people this app serves. A listing does not prove the test was completed.

The reader (`career-dashboard/backend/market/readers/eures.py`) uses the portal's public search
API, the same one its web pages use: no key, Ireland only, the person's target roles matched in
job titles, newest first, at most 4 pages of 50 per role and pass. europa.eu's robots.txt allows
these paths and asks for 10 seconds between requests; the fetcher keeps that pace. Each profile
keeps its own cursor (the newest vacancy it has read), so the next pass reads only newer ones.
A EURES id encodes the JobsIreland job number, which gives each lead its JobsIreland page: the
link people apply through. A few new matches per pass are read in detail for their closing date.

This API is not formally documented for third parties, so its contract is pinned by the fixture
in `tests/portable/test_eures_reader.py` (field names as recorded on 4 October 2026, synthetic
values). If EURES changes it, the reader reports the failure in Coverage and the other sources
continue. Review EURES's reuse terms before any hosted (multi-user) deployment.

## The shared market store

Every posting a source returns is also recorded in `career-dashboard/data/market/market.db`
(ignored by Git): the public posting, where it was read, the county, its kind (graduate
programme, internship, entry, experienced), role family and level, the posting's own permit
wording and its advertised pay. A profile's fit, saved jobs and applications never enter it.
Advertised salaries there give the market estimates shown when a posting states no pay: at least
five advertised annual salaries from three employers, for the same role family and level, in the
last 180 days, labelled "Market estimate from N advertised salaries (M employers). Not this
vacancy's pay." `career-dashboard/data/http_cache.db` keeps ETags so unchanged pages are re-read
cheaply.

## The employer registry

`career-dashboard/backend/countries/ie/employer-registry.csv` lists the careers boards of employers in
DETE's permit statistics, so searches read the employers most likely to hire on a permit directly,
from their own boards. Each row is a board that, on its `verified_on` date, listed at least one
current posting in Ireland and named the same employer as DETE's legal name: its own name equal to
it (`exact`), a curated alias (`alias`), at least 85% similar (`similar`), or the brand the legal
name extends with descriptor words (`brand`, the weakest: review these by hand before committing);
a board that states no name needs an Irish posting whose text names the employer (`text`). The
`name_check` column says which. Rows hold public data only: names, the board's address on its
applicant-tracking system and the counts it was chosen on.

Rebuild it when DETE publishes new figures (after refreshing the DETE CSV), from the repository root:

```text
career-dashboard\backend\.venv\Scripts\python.exe career-dashboard\backend\scripts\build_employer_registry.py --top 400 --ai kimi_cli
```

The builder takes the employers with the most permits in the last 24 complete months (leaving out
those already in `employers.yml`), tries at most three board names on Greenhouse, Lever (US and
EU), Ashby, SmartRecruiters, Recruitee, Personio and Teamtailor through the app's polite fetcher,
and keeps only accepted boards. Workable boards are never guessed (its API blocks a client after
some 70 guesses) and Workday boards cannot be guessed; both are checked from a link. `--ai
kimi_cli` (or `claude_code`, `codex`) asks a local AI app's web search for the job boards of the
largest employers still unresolved; every suggestion is checked exactly like a guess and never
accepted unchecked. `--links FILE` (CSV `legal_name,url`) checks links found by hand. What was
tried and refused is written to `career-dashboard/data/market/registry-review.json` (not
committed). At search time a board that answers "not found" is set aside for 30 days.

## Aggregators, source policy and the Tracker

Careerjet and Jooble are optional and need the person's own free key (Settings, Job sources, or
`career-dashboard/.env`). Careerjet's API also needs the public address of the person's
connection, which they whitelist in their Careerjet publisher account; the app never looks it up
itself. An aggregator result is an excerpt and a tracking link: the app looks each result up on
the employer's own board (tracked companies, the directory and the registry) and never follows the
tracking link itself, because that would register clicks on the person's own publisher account.
Unmatched results are kept as leads, shown with the aggregator's attribution for the person to
open. Jooble's free key allows 500 requests for its whole life; the app spends about ten a day.
Every source's kind, key, attribution, terms and request budget is in
`career-dashboard/backend/market/policy.py`.

The Tracker (its own tab) lists the shared market store: one row per role (the same role at the
same employer and place from several sources counts once, shown from the most direct source),
with the posting's own permit sentence, DETE's permit numbers for the employer (cached per employer
against the DETE data in use), advertised pay, closing dates and the person's own saved or applied
marks, which come from their profile and never enter the shared store. Exports are CSV and Excel;
a cell that would start a spreadsheet formula is kept as text. Saving a row re-reads the posting
from its source and runs the same gates as any other save.

The store fills as searches and hunts read their sources. The app also (switch
`graph_tracker_refresh`, on by default) re-reads the free public sources (the employer directory and registry, EURES,
gradireland and jobs.ie, never the keyed aggregators) for the roles of the ready profiles on the
computer every six hours, once per computer, then closes postings past their closing date and
marks long-unseen ones stale. `career market status` shows the counts and the last refresh;
`career market refresh [--force]` runs one by hand. No profile's jobs change.

The registry built on 2026-10-04 holds 6 boards out of the 384 largest permit employers not
already in the directory: most of the largest permit employers are hospitals and care, meat
processing and construction (vacancies on their own sites or Occupop), or large technology,
consulting and pharmaceutical companies on their own careers sites, SAP SuccessFactors, iCIMS or
Workday. The review file lists every link the AI search suggested; a Workday link checks like any
board, so adding one by hand (`--links`) is the way to extend the registry.

## Occupation lists

The source tables are the Department of Enterprise, Tourism and Employment's
[Critical Skills Occupations List](https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/economic-migration-policy/occupations-lists-and-reviews/csol.html)
and [Ineligible Occupations List](https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/economic-migration-policy/occupations-lists-and-reviews/iol.html).
Their official Word documents are parsed with Python's standard-library zip/XML tools. The importer carries
merged categories forward, preserves the employment wording and records every document URL, byte count,
SHA-256 and row count. No AI rewrites the lists.

Refresh from the repository root using the app's Python:

```text
career-dashboard\backend\.venv\Scripts\python.exe scripts\import_occupation_lists.py --save-originals .runtime\occupation-sources
```

On macOS/Linux use `career-dashboard/backend/.venv/bin/python`. Review the changed
`career-dashboard/backend/countries/ie/occupations.yml` before publishing. The importer discovers each DOCX
link from its official page and refuses changed table headers, inconsistent SOC codes and duplicate rows.
Both documents are validated before the bundled YAML is replaced. Original DOCX files stay in the ignored
runtime folder; commit the YAML only. An offline re-import can use `--source-dir .runtime/occupation-sources`;
it validates the documents but does not check whether DETE has since published an update. Only set
the required `--verified-at YYYY-MM-DD` to a date when the sources were actually checked.

The snapshot checked on 3 October 2026 has 60 Critical Skills rows and 188 Ineligible rows: 187 numbered
SOC-4 entries plus the generic private-home employment row. Spot-check SOC 2136 (programmers/software
development) and SOC 1259 (other service managers, with a Safety Manager exception) after each refresh.
The importer sets a 90-day review date; the deterministic matcher returns unknown after that date.

`occupation-keywords.yml` contains curated hints referencing real imported SOC codes. A title requires a
quoted matching duty excerpt. Conditional or narrow list entries retain their full specialism and exception
clauses and return unknown when those conditions have not been established. An explicitly verified SOC code can
match a curated whole-code entry; a code absent from both lists returns neither only when its mapping was
explicitly marked verified. A list match is dated occupation evidence and never establishes a person's
degree, registration, work permission or permit outcome. Not immigration advice.
