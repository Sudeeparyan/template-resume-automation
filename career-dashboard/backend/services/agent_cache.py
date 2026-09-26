"""Persistent AI stage cache and the daily limit on paid AI calls.

The limit is about money, so it counts only calls that are billed per call
(Azure OpenAI and any API key). Kimi Code, Codex and Claude Code run on her
plans: their real limit is each plan's own usage window, which the router
tracks (backend/ai/limits.py) and steps around, so their calls never count here.
"""
import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone

CACHE_VERSION = 'career-workers-v1'
DEFAULT_LIMIT = 6
MAX_LIMIT = 200
LIMIT_NOTE = ("Only paid calls count (Azure and API keys). Kimi Code, Codex and Claude Code use your plans: "
              "each has its own usage limit, and Auto moves to the next one when a plan is used up.")


def _free_ids() -> tuple:
    from backend.ai import router

    return router.FREE + (router.ID,)


def _paid_filter() -> tuple[str, tuple]:
    """SQL and parameters selecting the paid calls in ai_calls (anything not a free plan or Auto)."""
    free = _free_ids()
    return "provider IS NOT NULL AND provider NOT IN (" + ",".join("?" * len(free)) + ")", free


def paid_limit(service) -> int:
    return int((service.pref('ai_policy', {}) or {}).get('daily_call_limit', DEFAULT_LIMIT))


def paid_calls_today(service, db=None) -> int:
    """Paid calls today: background runs on a paid provider plus specialist calls metered on one."""
    where, params = _paid_filter()
    query = f"SELECT COUNT(*) FROM ai_calls WHERE day=? AND {where}"
    if db is not None:
        return db.execute(query, (service.today(), *params)).fetchone()[0]
    with service.w.connect() as own:
        return own.execute(query, (service.today(), *params)).fetchone()[0]


def paid_block(service) -> str | None:
    """Why a paid call may not run now, or None while today's paid limit has room."""
    limit = paid_limit(service)
    if limit <= 0:
        return "paid AI is switched off (the paid limit is 0)"
    if paid_calls_today(service) >= limit:
        return f"today's paid limit ({limit} calls) is used up"
    return None


class AgentCache:
    def __init__(self, service):
        self.s, self.w = service, service.w
        self.lock = threading.RLock()
        with self.w.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS ai_cache(key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT NOT NULL, web INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ai_calls(id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, day TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT);
            ''')

    def settings(self):
        return {'daily_call_limit': paid_limit(self.s)}

    def configure(self, limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_LIMIT:
            raise ValueError(f'The paid AI call limit must be between 0 and {MAX_LIMIT}')
        with self.w.connect() as db:
            self.s.set_pref('ai_policy', {'daily_call_limit': limit}, db)
            self.w.record_event(db, 'ai_policy_updated', daily_call_limit=limit)
        self.w.export_tracking()
        return self.stats()

    def stats(self):
        limit = paid_limit(self.s)
        with self.w.connect() as db:
            used = db.execute("SELECT COUNT(*) FROM ai_calls WHERE day=? AND cache_key != 'usage'", (self.s.today(),)).fetchone()[0]
            paid = paid_calls_today(self.s, db)
            cache = db.execute('SELECT COUNT(*), COALESCE(SUM(hits),0) FROM ai_cache').fetchone()
            by_provider = [
                {'provider': row[0], 'calls': row[1], 'input_tokens': row[2], 'output_tokens': row[3]}
                for row in db.execute(
                    """SELECT provider, COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0)
                    FROM ai_calls WHERE day=? GROUP BY provider ORDER BY provider""",
                    (self.s.today(),),
                )
            ]
        remaining = max(0, limit - paid)
        return {'daily_call_limit': limit, 'calls_today': used, 'paid_calls_today': paid,
                'free_calls_today': max(0, used - paid), 'remaining_calls': remaining,
                'paid_only': True, 'can_start': remaining > 0 or not self._main_is_paid(),
                'cached_results': cache[0], 'cache_hits': cache[1],
                'by_provider': by_provider, 'note': LIMIT_NOTE}

    def _main_is_paid(self) -> bool:
        """Whether background runs go to a paid provider by name (then the paid limit can stop them)."""
        from backend.ai import main_choice, ready_providers, router

        try:
            provider, _model = main_choice(self.s.pref('ai_preferences', {}) or {}, ready_providers(self.w.root))
        except Exception:
            return False
        return router.is_paid(provider)

    def execute(self, invoke, prompt, schema, *, cacheable=True, served_by=None, **options):
        """Run one AI call through the cache. ``served_by()`` names the endpoint Auto picked,
        so the call is recorded (and counted) against what actually answered."""
        from backend.ai import router

        key = hashlib.sha256(json.dumps([CACHE_VERSION, prompt, schema, options], sort_keys=True).encode()).hexdigest()
        with self.lock:
            # Reserve before invoking: concurrent workers cannot overspend the shared limit.
            with self.w.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT * FROM ai_cache WHERE key=?', (key,)).fetchone() if cacheable else None
                if old:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(old['created_at'])).total_seconds()
                    if not old['web'] or age < 7 * 86400:
                        db.execute('UPDATE ai_cache SET hits=hits+1 WHERE key=?', (key,))
                        return json.loads(old['result'])
                provider = options.get('provider', 'codex')
                model = options.get('model', 'codex-runtime')
                action = options.get('action', 'unknown')
                if router.is_paid(provider):
                    limit = paid_limit(self.s)
                    if paid_calls_today(self.s, db) >= limit:
                        raise ValueError(
                            f'Today\'s paid AI limit ({limit} calls) is used up, so {provider} was not called. '
                            'Saved results remain available. Choose Auto or a free plan (Kimi Code, Codex, '
                            'Claude Code) in Settings, or raise the paid limit there.')
                call_id = uuid.uuid4().hex
                db.execute(
                    """INSERT INTO ai_calls(id,cache_key,day,state,created_at,error,provider,model,action,cache_version)
                    VALUES(?,?,?,?,?,NULL,?,?,?,?)""",
                    (call_id, key, self.s.today(), 'running', self.s.now(), provider, model, action, CACHE_VERSION),
                )

            def record_served(db):
                served = served_by() if served_by else None
                if served:
                    db.execute("UPDATE ai_calls SET provider=?, model=? WHERE id=?", (served[0], served[1], call_id))

            try:
                result = invoke(prompt, schema, **options)
                # Only complete, schema-shaped reports can become reusable results.
                if not isinstance(result, dict) or any(k not in result for k in schema.get('required', [])):
                    raise ValueError('AI returned an incomplete structured result')
                encoded = json.dumps(result, ensure_ascii=False)
                with self.w.connect() as db:
                    db.execute("UPDATE ai_calls SET state='completed' WHERE id=?", (call_id,))
                    record_served(db)
                    if cacheable:
                        db.execute('INSERT INTO ai_cache VALUES(?,?,?,?,0) ON CONFLICT(key) DO UPDATE SET result=excluded.result,created_at=excluded.created_at',
                                   (key, encoded, self.s.now(), int(options.get('web', True))))
                return result
            except Exception as exc:
                with self.w.connect() as db:
                    db.execute("UPDATE ai_calls SET state='failed',error=? WHERE id=?", (str(exc)[:1000], call_id))
                    record_served(db)
                raise
