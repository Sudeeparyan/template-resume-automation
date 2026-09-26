"""Auto: one route across her AI plans, the way OpenRouter routes across providers.

Kimi Code (K3) first, then Codex, then Claude Code, and Azure (paid) only when
every free plan is resting or failing. A usage limit rests a plan until the
reset time it printed; the daily limit counts paid calls only.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

import backend.ai  # noqa: E402
from backend import paths  # noqa: E402
from backend.ai import claude_code, codex, keys, limits, router, settings as ai_settings  # noqa: E402
from backend.ai.agents.graph import AgentError, AgentTeam  # noqa: E402
from backend.ai.providers import AIGateway  # noqa: E402
from backend.services.agent_cache import AgentCache, paid_block  # noqa: E402
from backend.services.agents import AgentRunner, REPORT_SCHEMA  # noqa: E402
from test_career_workspace import workspace, add  # noqa: E402,F401
from test_workspace_v2 import service  # noqa: E402,F401

ALL_READY = {"kimi_cli": True, "codex": True, "claude_code": True, "azure_openai": True}
NOW = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
KIMI_LIMIT = "Your Kimi membership's usage limit is reached. It says: try again in 3 hours."
REPORT = {"summary": "s", "report": "r" * 200, "sources": [], "limitations": []}


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def plans(empty_ai_app_homes, monkeypatch):
    """Her plans as this PC lists them: Kimi K3, Codex GPT-6 models, one Azure deployment."""
    for name in keys.NAMES:
        monkeypatch.delenv(name, raising=False)
    codex_home = empty_ai_app_homes / "codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "priority": 1},
        {"slug": "gpt-6-sol", "display_name": "GPT-6-Sol", "priority": 2},
        {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "priority": 3},
    ]}), encoding="utf-8")
    kimi_home = empty_ai_app_homes / "kimi-code"
    kimi_home.mkdir(parents=True, exist_ok=True)
    (kimi_home / "config.toml").write_text(
        'default_model = "kimi-code/k3"\n[models."kimi-code/k3"]\nmodel = "k3"\ndisplay_name = "K3"\n'
        '[models."kimi-code/kimi-for-coding"]\nmodel = "kimi-for-coding"\ndisplay_name = "K2.8 Preview"\n',
        encoding="utf-8")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-6-luna")


def book(tmp_path, clock=None):
    return limits.HealthBook(tmp_path, clock or Clock())


def succeed_on(winner, calls, errors=None):
    """An attempt that records each (provider, model) and answers only on ``winner``."""
    def attempt(provider, model):
        calls.append((provider, model))
        if provider != winner:
            raise ValueError((errors or {}).get(provider, KIMI_LIMIT))
        return {"by": provider}
    return attempt


# --- recognising limits and reading reset times ----------------------------------

def test_limit_messages_are_recognised_and_other_errors_are_not():
    for text in ("Your Kimi membership's usage limit is reached.",
                 "You've hit your session limit · resets 5:40pm (America/Chicago)",
                 "Azure OpenAI returned HTTP 429: Too Many Requests",
                 "Claude AI usage limit reached|1759000000",
                 "gemini: the provider's rate limit or quota is exhausted",
                 "403 You've reached your 5-hour usage limit."):
        assert limits.is_limit(text), text
    for text in ("Claude Code reached its time limit. Nothing was saved from this call.",
                 "Codex returned output that did not match the requested schema",
                 "Kimi Code is not signed in on this machine: run `kimi login`, then retry."):
        assert not limits.is_limit(text), text


def test_reset_times_are_read_from_each_apps_wording():
    # 14:00 UTC is 9:00 in Chicago (Central Daylight Time).
    assert limits.parse_reset("You've hit your session limit · resets 5:40pm (America/Chicago)", NOW) == \
        datetime(2026, 9, 24, 22, 40, tzinfo=timezone.utc)
    assert limits.parse_reset("limit reached ∙ resets 8am (America/Chicago)", NOW) == \
        datetime(2026, 9, 25, 13, 0, tzinfo=timezone.utc)  # 8am has passed there: tomorrow
    assert limits.parse_reset("You've hit your usage limit. Try again in 2 hours 13 minutes.", NOW) == \
        NOW + timedelta(hours=2, minutes=13)
    assert limits.parse_reset("Claude AI usage limit reached|1759000000", NOW) == \
        datetime.fromtimestamp(1759000000, timezone.utc)
    dated = limits.parse_reset("try again at Sep 26th, 2026 3:05 PM", NOW).astimezone()
    assert (dated.month, dated.day, dated.hour, dated.minute) == (9, 26, 15, 5)
    assert limits.parse_reset("403 You've reached your 5-hour usage limit.", NOW) is None
    # Nothing readable: an hour for a plan, two minutes for a paid API; never more than a week.
    assert limits.rest_until("usage limit", now=NOW) == NOW + timedelta(minutes=60)
    assert limits.rest_until("HTTP 429", paid=True, now=NOW) == NOW + timedelta(minutes=2)
    assert limits.rest_until("try again in 30 days", now=NOW) == NOW + timedelta(days=7)


def test_each_runtime_names_its_limit_and_keeps_the_reset_time():
    said = codex.failure_reason('{"type":"error","message":"You\'ve hit your usage limit. Upgrade to Pro '
                                'or try again in 2 hours 13 minutes."}')
    assert said.startswith("Your ChatGPT plan's usage limit is reached") and "try again in 2 hours 13 minutes" in said
    assert limits.parse_reset(said, NOW) == NOW + timedelta(hours=2, minutes=13)
    claude = claude_code.describe_failure({"is_error": True, "subtype": "error",
                                           "result": "You've hit your session limit · resets 5:40pm (America/Chicago)"})
    assert "usage limit is reached" in claude and "resets 5:40pm (America/Chicago)" in claude
    # Other Codex failures keep their own words.
    assert codex.failure_reason('{"message": "Invalid schema for response_format"}') == "Invalid schema for response_format"


# --- the health book --------------------------------------------------------------

def test_a_rested_plan_wakes_at_its_reset_time_or_by_hand(tmp_path):
    clock = Clock()
    health = book(tmp_path, clock)
    health.rest_for_limit("kimi_cli", KIMI_LIMIT)
    assert health.resting("kimi_cli")["kind"] == "limit"
    clock.now = NOW + timedelta(hours=3, minutes=1)
    assert health.resting("kimi_cli") is None
    health.rest_for_limit("codex", "usage limit")
    health.wake("codex")
    assert health.resting("codex") is None


def test_three_failures_in_a_row_rest_a_plan_and_a_success_resets_the_count(tmp_path):
    health = book(tmp_path)
    for _ in range(2):
        health.record("codex", False, "Codex could not finish")
    health.record("codex", True)
    health.record("codex", False, "Codex could not finish")
    assert health.resting("codex") is None
    health.record("codex", False, "again")
    health.record("codex", False, "and again")
    rest = health.resting("codex")
    assert rest["kind"] == "failing" and rest["until"] == (NOW + timedelta(minutes=15)).isoformat(timespec="seconds")


def test_every_profile_sees_the_same_plan_state(tmp_path, monkeypatch):
    app = tmp_path / "app"
    monkeypatch.setattr(paths, "APP_ROOT", app)
    monkeypatch.setattr(paths, "PROFILES", app / "profiles")
    (app / "profiles" / "sri").mkdir(parents=True)
    limits.HealthBook(app).rest_for_limit("claude_code", "usage limit")
    assert limits.HealthBook(app / "profiles" / "sri").resting("claude_code")


# --- how much of each plan's 5-hour window is used --------------------------------------

def test_usage_windows_are_metered_and_a_plans_limit_is_learned_when_it_is_reached(tmp_path):
    clock = Clock()
    health = book(tmp_path, clock)
    health.meter("kimi_cli", 300_000)
    health.meter("kimi_cli", 200_000)
    usage = health.usage("kimi_cli")
    assert (usage["tokens"], usage["calls"], usage["capacity"], usage["percent"]) == (500_000, 2, 1_000_000, 50)
    assert usage["capacity_source"] == "guess" and usage["window_resets"] == (NOW + timedelta(hours=5)).isoformat(timespec="seconds")
    assert health.usage("kimi_cli", 2_000_000)["percent"] == 25  # the limit she typed wins
    assert health.usage("kimi_cli", 2_000_000)["capacity_source"] == "you"

    # Reaching the limit teaches it: this window allowed 500K. The next window starts empty.
    health.rest_for_limit("kimi_cli", "usage limit")
    learned = health.usage("kimi_cli")
    assert (learned["capacity"], learned["capacity_source"], learned["tokens"]) == (500_000, "learned", 0)

    # A tiny window says little (other apps share the plan), and a paid 429 never teaches a limit.
    health.meter("codex", 5_000)
    health.rest_for_limit("codex", "usage limit")
    assert health.usage("codex")["capacity_source"] == "guess"
    health.meter("azure_openai", 90_000)
    health.rest_for_limit("azure_openai", "HTTP 429", paid=True)
    assert "capacity_learned" not in json.loads(health.path.read_text())["providers"]["azure_openai"]

    # After five hours a new window starts.
    health.meter("claude_code", 50_000)
    clock.now = NOW + timedelta(hours=5, minutes=1)
    assert health.usage("claude_code")["tokens"] == 0
    health.meter("claude_code", 10_000)
    assert health.usage("claude_code")["tokens"] == 10_000


def test_the_specialist_team_meters_real_tokens_or_an_estimate(tmp_path):
    team = AgentTeam(tmp_path, {"strong": ("codex", "codex-runtime"), "cheap": ("codex", "codex-runtime")})
    team._meter("claude_code", {"input_tokens": 1000, "output_tokens": 500}, 0, False)
    team._meter("kimi_cli", {"input_tokens": None, "output_tokens": None}, 4000, True)
    team._meter("azure_openai", {"input_tokens": 1000, "output_tokens": 500}, 0, False)
    health = limits.HealthBook(tmp_path)
    assert health.usage("claude_code")["tokens"] == 1500
    assert health.usage("kimi_cli")["tokens"] == 1000 + limits.CALL_OVERHEAD["web"]
    assert "azure_openai" not in json.loads(health.path.read_text())["providers"]  # paid: the daily limit counts it


def test_limits_she_types_are_checked_and_kept_per_plan():
    policy = router.validate({"capacity": {"codex": 800_000, "kimi_cli": None}})
    assert policy["capacity"] == {"codex": 800_000}
    with pytest.raises(ValueError, match="between 10,000"):
        router.validate({"capacity": {"claude_code": 5}})


# --- the route --------------------------------------------------------------------

def test_kimi_goes_first_and_codex_takes_over_after_a_limit(tmp_path):
    health, calls, switches = book(tmp_path), [], []
    result, served = router.route(tmp_path, tier="strong", attempt=succeed_on("codex", calls),
                                  ready_map=ALL_READY, book=health, on_switch=switches.append)
    assert result == {"by": "codex"} and served == ("codex", "gpt-6-astra")
    assert calls == [("kimi_cli", "kimi-code/k3"), ("codex", "gpt-6-astra")]
    assert health.resting("kimi_cli")["until"] == (NOW + timedelta(hours=3)).isoformat(timespec="seconds")
    assert switches[0]["from_provider"] == "kimi_cli" and switches[0]["to_provider"] == "codex"
    assert "usage limit" in switches[0]["reason"]

    # While Kimi rests, the next step goes straight to Codex, with no new switch logged.
    calls.clear()
    router.route(tmp_path, tier="strong", attempt=succeed_on("codex", calls), ready_map=ALL_READY,
                 book=health, on_switch=switches.append)
    assert calls == [("codex", "gpt-6-astra")] and len(switches) == 1


def test_reading_steps_use_each_plans_lighter_model(tmp_path):
    calls = []
    router.route(tmp_path, tier="cheap", attempt=succeed_on("claude_code", calls), ready_map=ALL_READY,
                 book=book(tmp_path))
    assert calls == [("kimi_cli", "kimi-code/k3"), ("codex", "gpt-6-luna"), ("claude_code", "sonnet")]


def test_a_model_the_plan_no_longer_offers_falls_back_to_the_apps_default(tmp_path, empty_ai_app_homes):
    (empty_ai_app_homes / "codex" / "models_cache.json").write_text('{"models": []}', encoding="utf-8")
    (empty_ai_app_homes / "kimi-code" / "config.toml").write_text("", encoding="utf-8")
    assert router.model_for(tmp_path, "codex", "strong") == "codex-runtime"
    assert router.model_for(tmp_path, "kimi_cli", "strong") == "kimi-runtime"
    assert router.model_for(tmp_path, "azure_openai", "cheap") == "gpt-6-luna"


def test_other_errors_move_on_without_resting_the_plan(tmp_path):
    health, calls = book(tmp_path), []
    errors = {"kimi_cli": "Kimi Code did not return a JSON result. Retry."}
    _result, served = router.route(tmp_path, tier="strong", attempt=succeed_on("codex", calls, errors),
                                   ready_map=ALL_READY, book=health)
    assert served[0] == "codex" and health.resting("kimi_cli") is None
    assert "JSON" in health.status()["kimi_cli"]["last_error"]


def test_azure_is_last_even_when_listed_first_and_only_used_when_every_plan_is_out(tmp_path):
    health, calls = book(tmp_path), []
    policy = {"order": ["azure_openai", "kimi_cli", "codex", "claude_code"]}
    _result, served = router.route(tmp_path, tier="strong", policy=policy, attempt=succeed_on("kimi_cli", calls),
                                   ready_map=ALL_READY, book=health)
    assert served[0] == "kimi_cli" and calls == [("kimi_cli", "kimi-code/k3")]

    calls.clear()
    _result, served = router.route(tmp_path, tier="strong", attempt=succeed_on("azure_openai", calls),
                                   ready_map=ALL_READY, book=book(tmp_path / "fresh"))
    assert [c[0] for c in calls] == ["kimi_cli", "codex", "claude_code", "azure_openai"]
    assert served == ("azure_openai", "gpt-6-luna")


def test_a_used_up_paid_limit_keeps_azure_out_and_says_why(tmp_path):
    health, calls = book(tmp_path), []
    with pytest.raises(router.RouteError) as raised:
        router.route(tmp_path, tier="strong", attempt=succeed_on("nobody", calls), ready_map=ALL_READY,
                     book=health, paid_gate=lambda _p: "today's paid limit (5 calls) is used up")
    assert "azure_openai" not in [c[0] for c in calls]
    message = str(raised.value)
    assert "Azure OpenAI: today's paid limit (5 calls) is used up" in message
    assert "Kimi Code: usage limit reached" in message


def test_a_429_from_azure_rests_it_for_two_minutes(tmp_path):
    health, calls = book(tmp_path), []
    for plan in router.FREE:
        health.rest_for_limit(plan, "usage limit")
    with pytest.raises(router.RouteError, match="resting until"):
        router.route(tmp_path, tier="strong", ready_map=ALL_READY, book=health,
                     attempt=succeed_on("nobody", calls, {"azure_openai": "Azure OpenAI returned HTTP 429"}))
    assert calls == [("azure_openai", "gpt-6-luna")]
    assert health.resting("azure_openai")["until"] == (NOW + timedelta(minutes=2)).isoformat(timespec="seconds")


def test_switches_in_the_policy(tmp_path):
    calls = []
    router.route(tmp_path, tier="strong", policy={"enabled": {"kimi_cli": False}},
                 attempt=succeed_on("codex", calls), ready_map=ALL_READY, book=book(tmp_path))
    assert calls == [("codex", "gpt-6-astra")]

    calls.clear()
    with pytest.raises(ValueError, match="usage limit") as raised:
        router.route(tmp_path, tier="strong", policy={"allow_fallbacks": False},
                     attempt=succeed_on("codex", calls), ready_map=ALL_READY, book=book(tmp_path / "b"))
    assert not isinstance(raised.value, router.RouteError) and calls == [("kimi_cli", "kimi-code/k3")]

    with pytest.raises(ValueError, match="at least one free plan"):
        router.validate({"enabled": {"kimi_cli": False, "codex": False, "claude_code": False}})
    with pytest.raises(ValueError, match="Unknown AI"):
        router.validate({"order": ["gemini"]})
    assert router.normalise({"order": ["codex"]})["order"] == ["codex", "kimi_cli", "claude_code", "azure_openai"]


def test_gmail_work_runs_only_on_codex(tmp_path):
    health, calls = book(tmp_path), []
    router.route(tmp_path, tier="cheap", needs={"apps": True}, attempt=succeed_on("codex", calls),
                 ready_map=ALL_READY, book=health)
    assert calls == [("codex", "gpt-6-luna")]
    health.rest_for_limit("codex", "usage limit")
    with pytest.raises(router.RouteError) as raised:
        router.route(tmp_path, tier="cheap", needs={"apps": True}, attempt=succeed_on("codex", calls),
                     ready_map=ALL_READY, book=health)
    assert "Codex: resting until" in str(raised.value) and "Kimi Code: has no Gmail access" in str(raised.value)


# --- the specialist team -----------------------------------------------------------

def test_the_specialist_team_routes_auto_tiers(tmp_path, monkeypatch):
    team = AgentTeam(tmp_path, {"strong": ("auto", "auto"), "cheap": ("auto", "auto")},
                     route={"ready": ALL_READY, "on_switch": lambda event: None})
    tried = []

    def invoke_on(agent, text, provider, model, max_tokens):
        tried.append((provider, model))
        if provider == "kimi_cli":
            raise AgentError(f"{agent.name} could not run on kimi_cli/{model}: {KIMI_LIMIT}")
        return {"ok": True}

    monkeypatch.setattr(team, "_invoke_on", invoke_on)
    assert team.run("posting_parser", {"posting_text": "x"}) == {"ok": True}
    assert team.served[0] == "codex" and tried[0][0] == "kimi_cli"
    assert team.fell_back["from_provider"] == "kimi_cli" and team.fell_back["to_provider"] == "codex"
    assert team.fell_back["recorded"] is True


def test_a_paid_tier_stops_when_the_paid_limit_is_used_up(tmp_path):
    team = AgentTeam(tmp_path, {"strong": ("azure_openai", "gpt-6-luna"), "cheap": ("azure_openai", "gpt-6-luna")},
                     route={"paid_gate": lambda _p: "today's paid limit (0 calls) is used up"})
    with pytest.raises(AgentError, match="paid limit"):
        team.run("posting_parser", {"posting_text": "x"})


# --- the daily limit counts paid calls only ------------------------------------------

def _call(service, provider, day=None):
    import uuid

    with service.w.connect() as db:
        db.execute("""INSERT INTO ai_calls(id,cache_key,day,state,created_at,provider,model,action,cache_version)
                      VALUES(?,?,?,?,?,?,?,?,?)""",
                   (uuid.uuid4().hex, "k" + uuid.uuid4().hex, day or service.today(), "completed", service.now(),
                    provider, "m", "role_research", "v"))


def test_free_plan_calls_never_count_against_the_daily_limit(service):
    cache = AgentCache(service)
    cache.configure(2)
    for provider in ("kimi_cli", "codex", "claude_code", "auto", "codex"):
        _call(service, provider)
    stats = cache.stats()
    assert stats["paid_calls_today"] == 0 and stats["free_calls_today"] == 5 and stats["remaining_calls"] == 2
    assert paid_block(service) is None
    _call(service, "azure_openai")
    _call(service, "openai")
    assert cache.stats()["remaining_calls"] == 0 and "used up" in paid_block(service)

    # A free plan still runs with the paid limit used up; a paid provider by name does not.
    assert cache.execute(lambda *a, **k: REPORT, "free", REPORT_SCHEMA, provider="codex", model="codex-runtime") == REPORT
    with pytest.raises(ValueError, match="paid AI limit"):
        cache.execute(lambda *a, **k: REPORT, "paid", REPORT_SCHEMA, provider="azure_openai", model="gpt-6-luna")
    cache.configure(0)
    assert "switched off" in paid_block(service)


# --- the gateway, Settings and the default ------------------------------------------

def test_background_runs_on_auto_are_recorded_against_the_plan_that_answered(service, monkeypatch):
    monkeypatch.setattr(backend.ai, "ready_providers",
                        lambda root: {**ALL_READY, "azure_openai": False, "auto": True})
    runner = AgentRunner(service, execute=lambda *a, **k: REPORT)
    runner.gateway.providers["kimi_cli"].invoke = lambda *a, **k: (_ for _ in ()).throw(ValueError(KIMI_LIMIT))
    ai_settings.choose_main(service, runner.gateway, "auto", "auto")
    assert runner.cached("research this role", REPORT_SCHEMA, action="role_research") == REPORT
    with service.w.connect() as db:
        row = db.execute("SELECT provider, model FROM ai_calls WHERE cache_key != 'usage' ORDER BY created_at DESC").fetchone()
        switched = db.execute("SELECT details FROM activity WHERE action='provider_fallback'").fetchone()
    assert tuple(row) == ("codex", "gpt-6-astra")
    assert json.loads(switched[0])["from_provider"] == "kimi_cli"
    assert limits.HealthBook(service.w.root).resting("kimi_cli")


def test_auto_is_the_default_when_settings_holds_no_choice(service, monkeypatch):
    from backend.ai import main_choice, team_for

    monkeypatch.setattr(router, "DEFAULT_WHEN_UNSET", True)
    ready = {**ALL_READY, "auto": True}
    monkeypatch.setattr(backend.ai, "ready_providers", lambda root: ready)
    assert main_choice({}, ready) == ("auto", "auto")
    gateway = AIGateway(service, lambda *a, **k: REPORT)
    assert gateway.preferences()["default"]["provider"] == "auto"
    team = team_for(service)
    assert team.tiers == {"strong": ("auto", "auto"), "cheap": ("auto", "auto")} and team.fallback is None
    # Nothing on the route can run here: the older built-in default applies.
    assert main_choice({}, {"auto": False, "openai": False}) == ("codex", "codex-runtime")


def test_settings_show_the_route_save_it_and_wake_a_plan(service, monkeypatch):
    monkeypatch.setattr(backend.ai, "ready_providers", lambda root: {**ALL_READY, "auto": True})
    gateway = AIGateway(service, lambda *a, **k: REPORT)
    limits.HealthBook(service.w.root).rest_for_limit("codex", "usage limit. It says: try again in 2 hours.")
    overview = ai_settings.overview(service, gateway=gateway)
    auto = overview["providers"][0]
    assert auto["id"] == "auto" and auto["kind"] == "auto" and auto["configured"]
    rows = {row["provider"]: row for row in auto["endpoints"]}
    assert rows["codex"]["resting"]["until_text"] and rows["azure_openai"]["paid"]
    assert rows["kimi_cli"]["usage"]["capacity_source"] == "guess" and rows["azure_openai"]["usage"] is None

    saved = ai_settings.save_route(service, gateway, {"order": ["codex", "kimi_cli"], "enabled": {"claude_code": False}})
    route = saved["providers"][0]["route"]
    assert route["order"][:2] == ["codex", "kimi_cli"] and route["enabled"]["claude_code"] is False
    ai_settings.wake(service, gateway, "codex")
    assert limits.HealthBook(service.w.root).resting("codex") is None
    with pytest.raises(ValueError, match="at least one free plan"):
        ai_settings.save_route(service, gateway, {"enabled": {p: False for p in router.FREE}})

    chosen = ai_settings.choose_main(service, gateway, "auto", "auto")
    assert chosen["main"] == {"provider": "auto", "model": "auto"}
    assert service.pref("ai_preferences")["tiers"]["cheap"] == {"provider": "auto", "model": "auto"}
