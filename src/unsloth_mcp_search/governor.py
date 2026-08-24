# SPDX-License-Identifier: AGPL-3.0-only

"""Adaptive throttle, short-lived cache, and a hard per-call time budget that
sit between the MCP tool and `ddgs`.

Why this exists: `ddgs` fans each query across several engines with a per-engine
timeout, not a global one. Under a burst of agent searches the engines start
rate limiting this machine (slow or empty sweeps), a single call then walks
several slow engines back to back, and the total blows past the MCP client's
request timeout. The client reports that as an opaque `-32001 Request timed out`
with no hint that the cause was throttling.

This governor fixes both ends of that:
- A hard global budget per call (a worker thread joined with a deadline) means
  the tool always returns well before the client's timeout, with an actionable
  message instead of `-32001`.
- An additive-increase / multiplicative-decrease throttle spaces calls out only
  as much as the engines demand: near-zero delay when sweeps succeed, growing
  automatically when they come back throttled, then relaxing as calls recover.
- A short TTL cache serves repeated queries and refetched URLs for free, which
  is where measured redundancy actually is (repeated `url` reads).

Everything here is standalone glue so `web.py` stays a near-verbatim mirror of
Unsloth Studio and upstream resyncs stay diffable.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

# Search outcome the runner reports back, so the governor can both cache the
# good ones and read the engines' congestion signal from the bad ones.
OK = "ok"
EMPTY = "empty"
THROTTLED = "throttled"
ERROR = "error"


def _env_float(name: str, default: float) -> float:
    """Read a non-negative float from the environment, falling back on any
    unset or unparseable value so a bad override never takes the server down."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


# Hard ceiling on a single search, kept comfortably under the common 60s MCP
# request timeout so the caller sees this module's message, never a bare -32001.
_BUDGET_S = _env_float("UNSLOTH_MCP_SEARCH_BUDGET_S", 18.0)
# Delay floor between search starts when everything is healthy.
_BASE_INTERVAL_S = _env_float("UNSLOTH_MCP_SEARCH_MIN_INTERVAL_S", 0.3)
# Extra spacing added on each throttle signal, and the cap it climbs to. The
# cap plus the budget must stay under the client timeout: 6 + 18 = 24s < 60s.
_PENALTY_STEP_S = _env_float("UNSLOTH_MCP_SEARCH_PENALTY_STEP_S", 1.5)
_PENALTY_CAP_S = _env_float("UNSLOTH_MCP_SEARCH_PENALTY_CAP_S", 6.0)
# How much of the penalty a clean success sheds (multiplicative decrease).
_PENALTY_DECAY = _env_float("UNSLOTH_MCP_SEARCH_PENALTY_DECAY", 0.5)
# Concurrent in-flight searches past which new ones are refused fast rather than
# queued into the client timeout.
_MAX_INFLIGHT = _env_int("UNSLOTH_MCP_SEARCH_MAX_INFLIGHT", 4)
# Result cache lifetime. Search snippets and page text both age slowly.
_CACHE_TTL_S = _env_float("UNSLOTH_MCP_SEARCH_CACHE_TTL_S", 600.0)
_CACHE_MAX_ENTRIES = _env_int("UNSLOTH_MCP_SEARCH_CACHE_MAX_ENTRIES", 512)

_BUSY_MESSAGE = (
    "Search is busy: too many searches are running at once on this machine. "
    "Wait a few seconds and retry, run one search at a time, or read a known "
    'page directly with {"url": "<URL>"}.'
)
_BUDGET_MESSAGE = (
    "Search failed: the search engines are responding too slowly (likely rate "
    "limiting this machine). Wait a moment before searching again, space your "
    'searches out, or read a known page directly with {"url": "<URL>"}.'
)


class _Governor:
    """Process-wide throttle, cache, and budget shared by every search call.

    One instance lives for the lifetime of the stdio server, so its penalty
    state tracks the engines' mood across the whole session.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[object, tuple[float, str]] = {}
        self._inflight = 0
        # Monotonic time before which the next search start must wait.
        self._next_start = 0.0
        self._penalty = 0.0

    # -- cache -----------------------------------------------------------

    def cache_get(self, key: object) -> str | None:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit is None:
                return None
            expiry, text = hit
            if expiry <= now:
                self._cache.pop(key, None)
                return None
            return text

    def cache_put(self, key: object, text: str) -> None:
        if _CACHE_TTL_S <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if len(self._cache) >= _CACHE_MAX_ENTRIES:
                # Cheap prune: drop everything already expired, then, if still
                # full, the soonest-to-expire entry. Keeps the dict bounded
                # without a heap; the cache is small and reads dominate.
                dead = [k for k, (exp, _) in self._cache.items() if exp <= now]
                for k in dead:
                    self._cache.pop(k, None)
                if len(self._cache) >= _CACHE_MAX_ENTRIES:
                    oldest = min(self._cache, key=lambda k: self._cache[k][0])
                    self._cache.pop(oldest, None)
            self._cache[key] = (now + _CACHE_TTL_S, text)

    # -- admission and throttle -----------------------------------------

    def _reserve_slot(self) -> float | None:
        """Claim an in-flight slot and the next throttled start time.

        Returns the monotonic timestamp this call is cleared to start at, or
        None if too many searches are already running.
        """
        with self._lock:
            if self._inflight >= _MAX_INFLIGHT:
                return None
            self._inflight += 1
            now = time.monotonic()
            start_at = max(now, self._next_start) + _BASE_INTERVAL_S + self._penalty
            self._next_start = start_at
            return start_at

    def _release_slot(self) -> None:
        with self._lock:
            if self._inflight > 0:
                self._inflight -= 1

    def _note_outcome(self, status: str) -> None:
        """Move the throttle penalty on the engines' response. A throttled or
        budget-killed sweep raises it (additive, capped); a clean success
        decays it; an empty sweep nudges it up gently since ddgs surfaces a
        rate-limited sweep as an empty one."""
        with self._lock:
            if status in (THROTTLED, EMPTY):
                bump = _PENALTY_STEP_S if status == THROTTLED else _PENALTY_STEP_S / 2
                self._penalty = min(_PENALTY_CAP_S, self._penalty + bump)
            elif status == OK:
                self._penalty *= _PENALTY_DECAY
                if self._penalty < 0.05:
                    self._penalty = 0.0
            # ERROR leaves the penalty untouched: a fetch bug is not congestion.

    # -- the one entry point --------------------------------------------

    def run_search(self, runner: Callable[[], tuple[str, str]], cache_key: object) -> str:
        """Run a governed search.

        `runner` performs the raw ddgs call and returns `(status, text)` where
        status is one of OK / EMPTY / THROTTLED / ERROR. Returns the text to
        hand back to the caller, substituting a busy or budget message when the
        call is shed or runs past the hard budget.
        """
        cached = self.cache_get(cache_key)
        if cached is not None:
            return cached

        start_at = self._reserve_slot()
        if start_at is None:
            return _BUSY_MESSAGE

        box: dict[str, tuple[str, str]] = {}

        def worker() -> None:
            try:
                box["result"] = runner()
            except BaseException as exc:  # noqa: BLE001 - reported as text, never raised
                box["result"] = (ERROR, f"Search failed: {exc}")
            finally:
                self._release_slot()

        try:
            wait = start_at - time.monotonic()
            if wait > 0:
                time.sleep(min(wait, _BUDGET_S))
            thread = threading.Thread(target=worker, name="ddgs-search", daemon=True)
            thread.start()
            thread.join(_BUDGET_S)
        except BaseException:
            # Reserving succeeded but we never launched, or sleeping was
            # interrupted: hand the slot back so it is not leaked.
            self._release_slot()
            raise

        if "result" not in box:
            # Thread still running past the budget. It is a daemon and keeps its
            # own in-flight slot until it truly finishes, so it cannot pile up
            # past _MAX_INFLIGHT. Treat the stall as a throttle signal.
            self._note_outcome(THROTTLED)
            return _BUDGET_MESSAGE

        status, text = box["result"]
        self._note_outcome(status)
        if status == OK:
            self.cache_put(cache_key, text)
        return text


# Module-level singleton; import and use directly from web.py.
governor = _Governor()
