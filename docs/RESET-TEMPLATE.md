# Reset a used copy to an empty template

A GitHub clone starts with no profiles. Resetting is only needed when reusing a copy that
already contains people's data. This permanently deletes that copy's candidate documents,
jobs, chats, resumes, application history and local morning lists for **every profile**.

1. Stop the dashboard and morning runner. Disable any schedule that starts this copy while
   you are resetting it.
2. From the project folder, preview the exact paths:

   ```text
   python scripts/reset_template.py
   ```

3. After reviewing the paths, delete them:

   ```text
   python scripts/reset_template.py --apply
   ```

The preview changes nothing and never reads file contents. The reset removes all of
`career-dashboard/profiles/` (including its registry and trash), old `data/` and `output/`
under the app, private contents of `me/` and `my-jobs/`, generated morning lists and logs,
local Claude settings and schedules, old tests outside `tests/portable/`, and generated
runtime/cache files. The three shipped guides in `me/` and `my-jobs/` remain.

Source code, public country data, shared skills, installed Python/npm packages and the built
dashboard remain available. The next setup creates a new person from an empty profile list.

`backup/` and `.local-reference/` are never inspected or changed. Local `.env` files and
`keys.txt` are kept unless you explicitly use `--include-secrets` with the preview and apply
commands. The `.env.example` template always remains. Files inside erased private profile
folders are removed regardless of their filename.

The reset refuses symbolic links, Windows junctions or paths that escape the workspace.
It checks every planned tree before deleting anything. If an ordinary file is locked during
deletion, earlier paths may already have been removed; close the process holding the file,
preview again and rerun. A reset cannot undo deletions.

This utility does not change Windows Task Scheduler, AI app sign-ins, cloud files, external
chat history, backups or Git history. It does not certify that an arbitrary ZIP of the whole
folder is safe to share. Publish the clean Git tree, and run `python scripts/scan_release.py`
before committing. If private data was previously committed, deleting local files alone
does not remove it from Git history.
