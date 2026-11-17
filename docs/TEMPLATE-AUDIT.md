# Template audit

Verified initially on Windows on 2026-10-01, with a further audit on 2026-10-02. The three supplied reference applications were inspected
as code references. Changes were made only in this template, preserving existing uncommitted
work. Tests used disposable profiles; no application was submitted and no recruiter was contacted.

| Reference | Architecture compared with this template |
|---|---|
| USA resume builder | A single workspace database with fixed US policies. This template adds isolated profiles, country packs and Auto provider routing. The reference's proposed skills/projects and automatic application ghosting are incompatible with this template's evidence and status rules. |
| Two Ireland job finders | Profile-scoped React/FastAPI services and serialized agent workers. Their older preparation chains omit this template's posting recheck, independent PDF review and final readiness check. This template also adds employer feeds, focused search passes and the overnight hunt. |

The fixes cover these observed defects:

- Tailoring previously printed model-suggested skills and projects. It now selects registered
  content, restores exact registered project wording and resolves skills against their actual
  evidence. A keep decision cannot turn a suggestion into evidence.
- Unknown, held, missing and review-row evidence tags now fail validation in every section.
  Unsupported legacy claims block compilation and export, including older PDF and PNG
  artifacts. Legacy suggestion records remain available for those historical checks.
- A deleted PDF no longer remains current because its metadata still exists.
- Ireland location checks reject US namesakes such as Dublin, Ohio. A routine vetting or
  negated-clearance phrase no longer hides an independent sponsorship refusal in the same sentence.
- Morning lists use the same final readiness checks as Daily Search. Missing independent
  reviews, stale posting checks and unresolved evidence leave jobs pending. Reporting does
  not rewrite, compile or rescore saved artifacts.
- An unattended hunt without paid-AI permission cannot select a paid endpoint or use a paid
  fallback. Authorized paid calls remain supported.
- Frontend draft storage includes the profile identity. Profile-list failures do not enable
  personal workspace requests. Job lists and Assistant cards use each job's market wording.
- Shared career instructions consistently identify the selected profile. Setup instructions
  no longer classify a green card as citizenship. Standalone URL verification remains
  profile-independent.

The follow-up audit also fixed these defects:

- Overlapping frontend reads could restore older profile or setup state. Newer responses now
  win, saved market and authorization changes refresh build controls, and failed onboarding
  connections offer a retry. Blocked browser storage no longer prevents loading a saved draft.
- Damaged preview metadata could prevent opening or saving a resume and crash the Documents
  list. Such previews are now unavailable for export while their source remains editable.
- A completed independent review previously counted as passed regardless of its findings.
  Reviews now record a verdict and issues against the exact PDF/posting. Unresolved findings,
  malformed results and legacy reports cannot mark a resume ready; the UI shows the findings.
- Discovery requests with different search presets could share the wrong run. Reused runs
  now report their actual state; retries publish terminal results only after durable completion.
  Partial instruction edits are not automatically replayed, and workers share the same
  per-job write lock within a process.
- Positive sponsorship wording, unrelated event sponsorship, C2C wording and negated lists
  could misclassify permit restrictions. Triggering sentences retain their exact original text.
  Missing, malformed or recorded expired authorization requires confirmation; a refusal does
  not become an obstacle for someone whose saved permission needs no sponsorship.
- Known Northern Ireland locations and explicit foreign namesakes no longer pass as Republic
  of Ireland locations, while valid mixed locations remain available.
- Generated source-audit test profiles were publishable outside the normal disposable-test
  locations. Their scratch directory is now ignored and explicitly rejected by the privacy scan.
- Feed fetching tolerates absent response headers while retaining its global and per-host
  concurrency limits. Positive preparation fixtures now include advertised pay required by
  the existing Ireland salary policy rather than bypassing that policy.
- Explicit removal of a wrong kind of role again teaches automatic searches to avoid that
  title. Permit, location and seniority reasons remain specific to the removed vacancy.
  Interrupted-hunt tests now prove reuse from durable task records and matching artifacts;
  missing draft/PDF files rebuild. Research and study-plan artifact paths no longer duplicate
  the output directory, so changed or deleted reports invalidate their cached stage.

| Final check | Result |
|---|---|
| `Check Workspace.cmd` | Passed |
| Portable backend suite | 419 passed |
| Frontend suite | 121 passed in 20 files |
| TypeScript and Vite production build | Passed |
| Workspace/profile consistency | Passed; zero persistent profiles |
| Release privacy scan | Passed |
| Fresh installation, onboarding, source upload and profile isolation | Passed |

The complete Windows release gate was rerun after the follow-up fixes and reported
`All checks passed` on 2026-10-02. The final tree also passed `git diff --check`.

Agent schemas, provider fallback rules, hiring-manager payload isolation, country/page
contracts and real PDF compilation are covered by the tests. Providers, job boards and the
Task Scheduler are stubbed in the portable suite. Live provider authentication, real searches,
actual scheduled-task execution and a native macOS run were not exercised by this audit.

Non-blocking output: a dependency deprecation warning in Starlette's test client and Vite's
bundle-size advisory for the main JavaScript chunk. These did not fail the release check.
