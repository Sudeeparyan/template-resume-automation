"""Persistent search runs and change history shared by the UI and AI commands."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# career.py imports this module before it puts the app root on sys.path, so do it here.
sys.path.append(str(Path(__file__).resolve().parents[2]))
from backend.paths import TIMEZONE  # noqa: E402


def today(tz=None):
    """Today's date in `tz` (a profile's own time zone), else the default one."""
    return datetime.now(ZoneInfo(tz or TIMEZONE)).date().isoformat()


def valid_date(value):
    try:
        if datetime.strptime(value, '%Y-%m-%d').date().isoformat() != value:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('Date must be YYYY-MM-DD') from None
    return value


class Tracking:
    def init_tracking(self, db):
        db.executescript('''
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
                action TEXT NOT NULL, job_id TEXT, details TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS search_runs (
                date TEXT PRIMARY KEY, notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS search_jobs (
                run_date TEXT NOT NULL REFERENCES search_runs(date),
                job_id TEXT NOT NULL REFERENCES jobs(id),
                PRIMARY KEY (run_date, job_id));
        ''')

    def record_event(self, db, action, job_id=None, **details):
        db.execute('INSERT INTO activity(occurred_at,action,job_id,details) VALUES(?,?,?,?)',
                   (datetime.now(timezone.utc).isoformat(timespec='seconds'), action,
                    job_id, json.dumps(details, ensure_ascii=False)))

    def activity(self, limit=200):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM activity ORDER BY id DESC LIMIT ?', (limit,))
            return [{**dict(r), 'details': json.loads(r['details'])} for r in rows]

    def save_profile_notes(self, text):
        from career import atomic_write
        path = self.root/'data/context/UPDATES.md'
        old = path.read_text(encoding='utf-8') if path.exists() else ''
        with self.connect() as db:
            self.record_event(db, 'profile_notes_updated', before=old, after=text,
                              review_required=True)
        atomic_write(path, text)
        self.export_tracking()
        return {'saved': True, 'note': 'Update saved in your activity history. Review the evidence registry before using new facts on a resume.'}

    def start_search(self, date=None):
        date = valid_date(date or self.today())
        with self.connect() as db:
            changed = db.execute('INSERT OR IGNORE INTO search_runs(date,created_at) VALUES(?,?)',
                                 (date, datetime.now(timezone.utc).isoformat(timespec='seconds'))).rowcount
            if changed:
                self.record_event(db, 'search_started', date=date)
        self.export_tracking()
        return next(r for r in self.search_runs() if r['date'] == date)

    def track_search_job(self, job_id, date=None):
        job = self.get_job(job_id)
        if job.get('deleted_at'):
            raise ValueError('Restore this removed role before adding it to a search run')
        date = valid_date(date or self.today())
        self.start_search(date)
        with self.connect() as db:
            changed = db.execute('INSERT OR IGNORE INTO search_jobs VALUES(?,?)', (date, job_id)).rowcount
            if changed:
                self.record_event(db, 'search_job_added', job_id, date=date)
        self.export_tracking()
        return next(r for r in self.search_runs() if r['date'] == date)

    def update_search(self, date, notes):
        date = valid_date(date)
        self.start_search(date)
        with self.connect() as db:
            old = db.execute('SELECT notes FROM search_runs WHERE date=?', (date,)).fetchone()[0]
            db.execute('UPDATE search_runs SET notes=? WHERE date=?', (notes, date))
            self.record_event(db, 'search_notes_updated', date=date, before=old, after=notes)
        self.export_tracking()
        return next(r for r in self.search_runs() if r['date'] == date)

    def search_runs(self):
        with self.connect() as db:
            result = []
            for row in db.execute('SELECT * FROM search_runs ORDER BY date DESC'):
                jobs = [dict(j) for j in db.execute('SELECT j.* FROM jobs j JOIN search_jobs s ON j.id=s.job_id WHERE s.run_date=? ORDER BY j.company,j.id', (row['date'],))]
                result.append({**dict(row), 'jobs': jobs})
            return result

    def export_history(self):
        from career import atomic_write
        atomic_write(self.root/'data/jobs.json', json.dumps(self.jobs(), indent=2, ensure_ascii=False)+'\n')
        atomic_write(self.root/'data/activity.json', json.dumps(self.activity(limit=-1), indent=2, ensure_ascii=False)+'\n')
        for run in self.search_runs():
            atomic_write(self.daily_dir/run['date']/'run.json', json.dumps(run, indent=2, ensure_ascii=False)+'\n')
