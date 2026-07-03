"""Simple in-memory sliding-window rate limiter.

Per-process (like the session stores; gunicorn runs a single worker) and
thread-safe (gthread worker runs 16 threads). State is keyed by caller-chosen
strings, typically "<action>:<session id>".
"""
import time
import threading

_lock = threading.Lock()
_events = {}  # key -> list of event timestamps


def allow(key, max_events, window_seconds, now=None):
    """Record an event for key and return True, or False if the key already
    hit max_events within the last window_seconds (event not recorded)."""
    if now is None:
        now = time.time()
    with _lock:
        events = [t for t in _events.get(key, []) if now - t < window_seconds]
        if len(events) >= max_events:
            _events[key] = events
            return False
        events.append(now)
        _events[key] = events
        return True


def reset():
    """Clear all rate-limit state (tests)."""
    with _lock:
        _events.clear()
