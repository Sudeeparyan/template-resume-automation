"""Career Dashboard backend.

New domain services live here while the legacy import paths remain as thin
compatibility adapters during the staged five-folder migration.

Importing the backend keeps candidate data on this computer: LangChain's hosted
tracing (LangSmith) would upload prompts, CVs and immigration status, so it is
switched off in every process that uses the backend unless the person explicitly
allows it with CAREER_ALLOW_LANGSMITH=1 (docs/OBSERVABILITY.md).

It also checks HTTPS certificates the way the computer's browser does (the operating
system's trust store, through ``truststore``): Python's own store on Windows lacks roots
that Windows fetches on demand, so official sites such as europa.eu (EURES) failed there.
"""

import os

BACKEND_VERSION = "2026.09"


def use_system_certificates() -> bool:
    """Verify HTTPS with the operating system's trust store; False when truststore is missing."""
    try:
        import truststore

        truststore.inject_into_ssl()
        return True
    except (ImportError, RuntimeError, AttributeError):
        return False

HOSTED_TRACING = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


def keep_traces_local(environ=None):
    """Turn hosted LLM tracing off unless CAREER_ALLOW_LANGSMITH=1; returns the environment."""
    env = os.environ if environ is None else environ
    if env.get("CAREER_ALLOW_LANGSMITH") != "1":
        for name in HOSTED_TRACING:
            env[name] = "false"
    return env


keep_traces_local()
use_system_certificates()
