# Data contract

`data/context/evidence.yml` and `data/config/profile.yml` govern Chetan's candidate facts, target
roles, Ireland policy and resume requirements. The active Profile view may hold user edits that await
canonical evidence reconciliation. Raw source documents and historical packs are provenance; they
never supply a new resume claim by themselves.

Every visible claim in a resume must cite a registered evidence ID. Held and missing entries stay off
resumes. Company research and job descriptions help select relevant facts but do not create candidate
experience. Study-plan skills remain separate until Chetan confirms them and they are registered.

`data/career.db` is the only mutable store for jobs, statuses, activity, goals, knowledge, mail evidence
and agent runs. JSON and Markdown trackers under `data/` and dated `daily-job-search/` folders are
generated projections. Update state through `Workspace`, `CareerServices`, the API or the `career`
CLI, never by hand-editing projections. Preserve explicit application dates and receipt dates
separately. Do not infer a submission, rejection or ghosted status from elapsed time.

Each prepared application uses its saved JD and an isolated folder under
`data/output/applications/`. Required artifacts include `job-description.md`, `evaluation.md`,
`company-research.md`, `evidence-map.yml`, `resume.tex`, `resume.pdf`, both preview PNGs, `qa.json` and
`study-plan.md`. The resume must contain exactly two distinct registered projects on two A4 pages,
with 10–12pt body text, a single column, current hashes and visual review of both pages. A lead
project may be used for multiple employers when it is the strongest truthful match.
