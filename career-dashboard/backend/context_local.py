"""Per-run state that follows a run into the worker threads it starts.

``threading.local`` belongs to one thread. A run that fans work out loses it: LangGraph runs
parallel nodes in a thread pool, and telemetry.submit/carry hand work to pools. ContextLocal
keeps the same attribute interface but stores the values in a ``contextvars.ContextVar``,
which LangGraph and telemetry.submit/carry copy into the threads they use.

Every write makes a new mapping (copy on write), so two parallel branches never see each
other's changes and a value set in a worker never leaks back to the code that started it.
Store only small immutable values here (ids, names, flags); shared buffers belong in an
object guarded by a lock.
"""

from __future__ import annotations

import contextvars
from itertools import count

_NAMES = count()


class ContextLocal:
    __slots__ = ("_var",)

    def __init__(self, name: str = "context_local"):
        object.__setattr__(self, "_var", contextvars.ContextVar(f"{name}-{next(_NAMES)}", default=None))

    def __getattr__(self, name: str):
        values = object.__getattribute__(self, "_var").get()
        if values is not None and name in values:
            return values[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        var = object.__getattribute__(self, "_var")
        var.set({**(var.get() or {}), name: value})

    def __delattr__(self, name: str) -> None:
        var = object.__getattribute__(self, "_var")
        values = dict(var.get() or {})
        if name not in values:
            raise AttributeError(name)
        del values[name]
        var.set(values)
