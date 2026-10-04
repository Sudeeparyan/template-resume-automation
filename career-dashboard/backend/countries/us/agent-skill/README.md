# Dormant: the US job-sources agent skill

This copy searches Ireland only (`countries/markets.yml`), so the US skill is kept here,
outside the folders AI apps read, instead of being deleted.

To offer the US market again:

1. Add `us` to `enabled:` in `career-dashboard/backend/countries/markets.yml`.
2. Copy `us-job-sources/` to `.agents/skills/us-job-sources/` and
   `claude-pointer/SKILL.md` to `.claude/skills/us-job-sources/SKILL.md`.
3. Restore the AGENTS.md routing row and the `find-jobs` mention (see
   `docs/DEVELOPERS.md`, "Re-enabling a market").
4. Run `Check Workspace.cmd`; `validate_workspace.py` checks the skill folders match the
   enabled markets.
