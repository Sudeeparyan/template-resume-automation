"""Which failures a graph node retries: transient network trouble only.

An AI plan that has reached its usage limit is never retried in a loop: the limit error ends
the step, the run records it, and the hunt waits for the plan to reset (services/hunt.py) or
the person is told. Wrong output, a refused page or a bug is not retried either.
"""

from __future__ import annotations

from langgraph.types import RetryPolicy

_TRANSIENT = ("timeout", "timed out", "connection reset", "connection aborted", "connection refused",
              "temporarily", "temporary failure", "unreachable", "http 502", "http 503", "http 504")


def transient(error: BaseException) -> bool:
    from backend.ai import limits

    text = str(error).casefold()
    if limits.is_limit(text):
        return False  # a plan's limit is waited out, never hammered
    return isinstance(error, (TimeoutError, ConnectionError)) or any(word in text for word in _TRANSIENT)


NETWORK = RetryPolicy(max_attempts=3, initial_interval=2.0, backoff_factor=2.0, max_interval=30.0, jitter=True,
                      retry_on=transient)
