"""Azure OpenAI: an Azure key is recognised wherever it is pasted and never sent to api.openai.com.

Azure AI Foundry keys (84 characters, JQQJ99 signature) were being pasted as
OPENAI_API_KEY; the OpenAI provider would then be "ready" and every run would
fail against api.openai.com. They are filed as AZURE_OPENAI_API_KEY instead, and
Azure is ready only once the endpoint and a deployment name are in .env too.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.ai import catalog, keys, models  # noqa: E402
from backend.ai.providers import (  # noqa: E402
    CAPABLE_ORDER, AIGateway, AzureOpenAIProvider, HostedProvider, OpenAIProvider)

AZURE_LIKE = "A" * 52 + "JQQJ99" + "B" * 26


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in keys.NAMES + keys.SETTINGS:
        monkeypatch.delenv(name, raising=False)
    app = tmp_path / "career-dashboard"
    app.mkdir()
    return app


def test_an_azure_key_is_filed_as_azure_bare_or_named_openai(root):
    (root / ".env").write_text(AZURE_LIKE + "\n")
    assert keys.secret(root, "AZURE_OPENAI_API_KEY") == AZURE_LIKE
    (root / ".env").write_text("OPENAI_API_KEY=" + AZURE_LIKE + "\n")
    assert keys.secret(root, "AZURE_OPENAI_API_KEY") == AZURE_LIKE
    assert keys.secret(root, "OPENAI_API_KEY") == ""
    assert OpenAIProvider(root).configured is False  # never sent to api.openai.com


def test_azure_is_ready_only_with_endpoint_and_deployment(root):
    (root / ".env").write_text("AZURE_OPENAI_API_KEY=" + AZURE_LIKE + "\n")
    listed = catalog.models(root, catalog.AZURE)
    assert listed["configured"] is False
    assert "AZURE_OPENAI_ENDPOINT" in listed["error"] and "AZURE_OPENAI_DEPLOYMENT" in listed["error"]
    assert models.available(root)[catalog.AZURE] is False
    with pytest.raises(models.ProviderNotConfigured, match="AZURE_OPENAI_ENDPOINT"):
        models.build(root, catalog.AZURE, "gpt-luna")

    (root / ".env").write_text(
        "AZURE_OPENAI_API_KEY=" + AZURE_LIKE + "\n"
        "AZURE_OPENAI_ENDPOINT=https://chetan-ai.openai.azure.com/\n"
        "AZURE_OPENAI_DEPLOYMENT=gpt-luna, gpt-luna-mini\n"
    )
    assert models.available(root)[catalog.AZURE] is True
    provider = HostedProvider(root, catalog.AZURE)
    assert provider.configured and provider.models == ("gpt-luna", "gpt-luna-mini")
    assert catalog.defaults(catalog.AZURE, root) == {"strong": "gpt-luna", "cheap": "gpt-luna"}
    llm = models.build(root, catalog.AZURE, "gpt-luna")
    assert llm.model_name == "gpt-luna"
    assert str(llm.openai_api_base) == "https://chetan-ai.openai.azure.com/openai/v1/"
    # Verified live against gpt-6-luna: it refuses temperature 0 and refuses function
    # tools with reasoning on chat completions, so Azure goes through the Responses API.
    assert llm.temperature is None and llm.use_responses_api is True


def test_a_full_v1_endpoint_is_kept_as_given(root):
    (root / ".env").write_text("AZURE_OPENAI_ENDPOINT=https://chetan.services.ai.azure.com/openai/v1\n")
    assert catalog.azure_settings(root)["base_url"] == "https://chetan.services.ai.azure.com/openai/v1/"


def configured(root):
    (root / ".env").write_text(
        "AZURE_OPENAI_API_KEY=" + AZURE_LIKE + "\n"
        "AZURE_OPENAI_ENDPOINT=https://chetan-ai.openai.azure.com/\n"
        "AZURE_OPENAI_DEPLOYMENT=gpt-luna, gpt-luna-mini\n"
    )
    return root


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "note": {"type": "string", "default": ""}}}


def answered(text):
    return {"status": "completed", "output": [
        {"type": "web_search_call"}, {"type": "reasoning"},
        {"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def test_azure_searches_the_web_through_the_responses_api(root):
    """Verified live against gpt-6-luna: the web_search tool runs several searches, then answers in the schema."""
    configured(root)
    sent = []

    def transport(url, payload, headers, timeout):
        sent.append((url, payload, headers, timeout))
        return answered('{"ok": true, "note": "found"}')

    provider = AzureOpenAIProvider(root, transport=transport)
    assert "web" in provider.capabilities
    assert provider.generate("find jobs", SCHEMA, model="gpt-luna") == {"ok": True, "note": "found"}
    url, payload, headers, timeout = sent[0]
    assert url == "https://chetan-ai.openai.azure.com/openai/v1/responses"
    assert headers == {"api-key": AZURE_LIKE} and timeout == AzureOpenAIProvider.TIMEOUT
    assert payload["model"] == "gpt-luna" and payload["tools"] == [{"type": "web_search"}]
    shape = payload["text"]["format"]
    # Strict: every field required, objects closed, no defaults.
    assert shape["strict"] is True and shape["schema"]["required"] == ["ok", "note"]
    assert shape["schema"]["additionalProperties"] is False and "default" not in json.dumps(shape["schema"])


def test_a_rate_limited_azure_call_waits_and_retries(monkeypatch):
    """Live on 23 Sep: a job search started within a minute of the last was refused with 429."""
    import io
    from urllib.error import HTTPError
    from backend.ai import providers

    def refused(wait):
        body = io.BytesIO(b'{"error": {"message": "Your requests to gpt-6-luna have exceeded rate limit."}}')
        return HTTPError("https://x/responses", 429, "Too Many Requests", {"retry-after": wait}, body)

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    replies = [refused("7"), refused(None), refused("40"), Answer(b'{"status": "completed"}')]

    def urlopen(request, timeout):
        reply = replies.pop(0)
        if isinstance(reply, HTTPError):
            raise reply
        return reply

    monkeypatch.setattr(providers, "urlopen", urlopen)
    waits = []
    assert providers._azure_request("https://x/responses", {}, {}, 5, sleep=waits.append) == {"status": "completed"}
    # Never shorter than the growing floor (Azure's "1 s" hint outlived the token window on
    # 23 Sep); longer when Azure asks for longer, and never above a minute.
    assert waits == [15, 30, 45]

    replies[:] = [refused("120")] * (providers.RATE_LIMIT_RETRIES + 1)
    waits.clear()
    with pytest.raises(ValueError, match="HTTP 429: Your requests to gpt-6-luna have exceeded rate limit"):
        providers._azure_request("https://x/responses", {}, {}, 5, sleep=waits.append)
    assert waits == [providers.RATE_LIMIT_MAX_WAIT] * providers.RATE_LIMIT_RETRIES


def test_azure_without_the_web_keeps_the_langchain_path(root, monkeypatch):
    configured(root)
    seen = []
    monkeypatch.setattr(HostedProvider, "generate", lambda self, prompt, schema, **options: seen.append(options) or {"ok": True})
    provider = AzureOpenAIProvider(root, transport=lambda *a: pytest.fail("no web call expected"))
    assert provider.generate("rewrite", SCHEMA, model="gpt-luna", web=False) == {"ok": True}
    assert seen == [{"model": "gpt-luna"}]


def test_an_unfinished_or_unreadable_azure_answer_is_named(root):
    configured(root)
    stopped = AzureOpenAIProvider(root, transport=lambda *a: {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})
    with pytest.raises(ValueError, match="stopped before finishing .max_output_tokens."):
        stopped.generate("find", SCHEMA, model="gpt-luna")
    garbled = AzureOpenAIProvider(root, transport=lambda *a: answered("I could not search today."))
    with pytest.raises(ValueError, match="did not match the requested schema"):
        garbled.generate("find", SCHEMA, model="gpt-luna")
    with pytest.raises(ValueError, match="Unsupported Azure OpenAI model"):
        garbled.generate("find", SCHEMA, model="gpt-9")


def test_azure_lists_her_deployments_after_the_env_ones(root, monkeypatch):
    configured(root)
    asked = []

    def request(url, headers, timeout=20):
        asked.append((url, headers))
        return {"data": [{"id": "gpt-5.1-eu", "model": "gpt-5.1", "status": "succeeded"},
                         {"id": "gpt-luna", "model": "gpt-6-luna", "status": "succeeded"},
                         {"id": "half-made", "model": "gpt-5.1", "status": "creating"}]}

    monkeypatch.setattr(catalog, "_request", request)
    listed = catalog.models(root, catalog.AZURE)
    assert listed["models"] == ["gpt-luna", "gpt-luna-mini", "gpt-5.1-eu"] and listed["error"] is None
    assert asked == [("https://chetan-ai.openai.azure.com" + catalog.AZURE_DEPLOYMENTS_PATH, {"api-key": AZURE_LIKE})]
    assert catalog.azure_deployments(root) == ["gpt-luna", "gpt-luna-mini", "gpt-5.1-eu"]
    assert catalog.azure_details(root) == {"gpt-5.1-eu": "gpt-5.1", "gpt-luna": "gpt-6-luna"}
    assert AzureOpenAIProvider(root).models == ("gpt-luna", "gpt-luna-mini", "gpt-5.1-eu")
    catalog.models(root, catalog.AZURE)  # cached for a day: no second request
    assert len(asked) == 1


def test_the_gateway_sends_job_search_to_azure_when_she_chooses_it(root):
    configured(root)
    service = SimpleNamespace(w=SimpleNamespace(root=root), pref=lambda key, default=None: default)
    gateway = AIGateway(service, codex_invoke=lambda *a, **k: {})
    provider, model = gateway.resolve("discovery", "azure_openai", "gpt-luna")
    assert provider.id == "azure_openai" and model == "gpt-luna"
    assert "azure_openai" in CAPABLE_ORDER
