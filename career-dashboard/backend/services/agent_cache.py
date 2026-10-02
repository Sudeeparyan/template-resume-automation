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
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone

CACHE_VERSION = 'career-workers-v2'
DEFAULT_LIMIT = 6
MAX_LIMIT = 200
LIMIT_NOTE = ("Only paid calls count (Azure and API keys). Kimi Code, Codex and Claude Code use your plans: "
              "each has its own usage limit, and Auto moves to the next one when a plan is used up.")

_reservation = ContextVar('career_paid_reservation', default=None)
_flights_guard = threading.Lock()
_flights = {}


@contextmanager
def _singleflight(root, key):
    """Share one in-flight cache key across service instances, without blocking other keys."""
    identity = (str(root.resolve()), key)
    with _flights_guard:
        flight = _flights.setdefault(identity, [threading.RLock(), 0])
        flight[1] += 1
    try:
        with flight[0]:
            yield
    finally:
        with _flights_guard:
            flight[1] -= 1
            if not flight[1]:
                _flights.pop(identity, None)


def current_call_id(service=None, provider=None):
    active = _reservation.get()
    if active and service is not None and active['root'] != str(service.w.root.resolve()):
        return None
    if active and provider is not None and active['provider'] != provider:
        return None
    return active['id'] if active else None


def _budget_schema(service):
    with service.w.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        columns = {row[1] for row in db.execute('PRAGMA table_info(ai_calls)')}
        if 'budget_reserved' not in columns:
            db.execute('ALTER TABLE ai_calls ADD COLUMN budget_reserved INTEGER NOT NULL DEFAULT 0')


@contextmanager
def paid_invocation(service, provider, model, action):
    """Atomically reserve each actual paid endpoint call, including failed attempts.

Retries reserve separately. Telemetry updates this row via current_call_id(),
so a cached operation and its usage record never count the same call twice.
"""
    from backend.ai import router

    if not router.is_paid(provider):
        yield None
        return
    identity = str(service.w.root.resolve())
    active = _reservation.get()
    if active and active['root'] == identity and active['provider'] == provider:
        yield active['id']
        return
    _budget_schema(service)
    call_id = uuid.uuid4().hex
    with service.w.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        limit = paid_limit(service)
        if paid_calls_today(service, db) >= limit:
            raise ValueError(f"Today's paid AI limit ({limit} calls) is used up; {provider} was not called.")
        db.execute('''INSERT INTO ai_calls
            (id,cache_key,day,state,created_at,provider,model,action,cache_version,budget_reserved)
            VALUES(?,?,?,?,?,?,?,?,?,1)''', (call_id, 'reservation', service.today(), 'running',
            service.now(), provider, model, action, CACHE_VERSION))
    token = _reservation.set({'id': call_id, 'provider': provider, 'root': identity})
    try:
        yield call_id
        with service.w.connect() as db:
            db.execute("UPDATE ai_calls SET state='completed' WHERE id=?", (call_id,))
    except Exception as error:
        with service.w.connect() as db:
            db.execute("UPDATE ai_calls SET state='failed',error=? WHERE id=?", (str(error)[:1000], call_id))
        raise
    finally:
        _reservation.reset(token)


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
    if db is not None:
        columns = {row[1] for row in db.execute('PRAGMA table_info(ai_calls)')}
        # v2 cache rows describe logical operations; reservations describe actual paid calls.
        reserved = " AND (budget_reserved=1 OR COALESCE(cache_version,'')!=?)" if 'budget_reserved' in columns else ''
        query = f"SELECT COUNT(*) FROM ai_calls WHERE day=? AND {where}{reserved}"
        if reserved:
            return db.execute(query, (service.today(), *params, CACHE_VERSION)).fetchone()[0]
        return db.execute(query, (service.today(), *params)).fetchone()[0]
    with service.w.connect() as own:
        return paid_calls_today(service, own)


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
        _budget_schema(service)

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
        free = _free_ids()
        # Logical v2 cache operations are separate from actual paid reservations.
        # Free specialist telemetry still represents a real call of its own.
        actual = "(budget_reserved=1 OR cache_version!=? OR provider IN (" + ",".join("?" * len(free)) + "))"
        params = (self.s.today(), CACHE_VERSION, *free)
        with self.w.connect() as db:
            used = db.execute("SELECT COUNT(*) FROM ai_calls WHERE day=? AND " + actual, params).fetchone()[0]
            paid = paid_calls_today(self.s, db)
            cache = db.execute('SELECT COUNT(*), COALESCE(SUM(hits),0) FROM ai_cache').fetchone()
            by_provider = [
                {'provider': row[0], 'calls': row[1], 'input_tokens': row[2], 'output_tokens': row[3]}
                for row in db.execute(
                    """SELECT provider, COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0)
                    FROM ai_calls WHERE day=? AND """ + actual + " GROUP BY provider ORDER BY provider",
                    params,
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

    def execute(self, invoke, prompt, schema, *, cacheable=True, served_by=None,
                managed_budget=False, **options):
        """Reuse a grounded input's result; network I/O never holds a global cache lock.

        A gateway caller sets managed_budget=True because it reserves the actual
        endpoint after routing. Legacy callers reserve their named endpoint here.
        """
        key = hashlib.sha256(json.dumps([CACHE_VERSION, prompt, schema, options], sort_keys=True).encode()).hexdigest()
        # A per-key lock permits unrelated calls to proceed. The cache is re-read
        # after acquisition so concurrent identical requests consume one invocation.
        flight = _singleflight(self.w.root, key) if cacheable else nullcontext()
        with flight:
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
                reservation = (nullcontext() if managed_budget else
                               paid_invocation(self.s, provider, model, action))
                with reservation:
                    result = invoke(prompt, schema, **options)
                    # Cache only complete reports. Specialist Pydantic validation
                    # and source-evidence checks remain authoritative downstream.
                    if not isinstance(result, dict) or any(k not in result for k in schema.get('required', [])):
                        raise ValueError('AI returned an incomplete structured result')
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
                with self.w.connect() as db:
                    db.execute("UPDATE ai_calls SET state='completed' WHERE id=?", (call_id,))
                    record_served(db)
                    if cacheable:
                        db.execute('INSERT INTO ai_cache VALUES(?,?,?,?,0) ON CONFLICT(key) DO UPDATE SET result=excluded.result,created_at=excluded.created_at,web=excluded.web',
                                   (key, encoded, self.s.now(), int(options.get('web', True))))
                return result
            except Exception as exc:
                with self.w.connect() as db:
                    db.execute("UPDATE ai_calls SET state='failed',error=? WHERE id=?", (str(exc)[:1000], call_id))
                    record_served(db)
                raise
