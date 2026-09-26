# Your job lists

When the dashboard is not installed, your AI app writes your jobs here. Git never uploads this
folder; only this guide is shared.

```text
my-jobs/
  tracker.csv                     every job you were shown, once each, and what you did with it
  2026-10-05/
    JOBS.md                       that day's list: fit, why, permit note, apply link, resume
    01-example-co-data-analyst/
      job.md                      the posting as read that day, the link and the fit notes
      Ada-Lovelace-CV.pdf         the resume made for this job (and a .docx copy)
```

`tracker.csv` has one line per job:

```text
date_found,company,title,location,url,fit,status,applied_on,resume,notes
```

`status` is `suggested`, `applied`, `interview`, `offer`, `rejected`, `withdrawn` or
`not-interested`. Tell your AI app "I applied to Example Co" or "not interested in Example Co" and
it updates the line. A job you applied for or turned down is never suggested again. Nothing is
ever submitted for you and no employer is contacted.

With the dashboard installed, your lists are in the app instead (see START-HERE.md).
