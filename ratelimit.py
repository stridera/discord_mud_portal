import time


class RateLimiter:
    """A simple per-key sliding-window rate limiter.

    `now` is injectable so tests can drive the clock without real sleeps.
    Not asyncio-specific and has no side effects beyond its own internal
    bookkeeping, so it's plain-old-object testable.
    """

    def __init__(self, max_events: int, window: float, now=time.monotonic):
        self.max_events = max_events
        self.window = window
        self._now = now
        self._events: dict[object, list[float]] = {}

    def _prune(self, key: object) -> list[float]:
        events = self._events.get(key)
        if not events:
            return []
        cutoff = self._now() - self.window
        while events and events[0] < cutoff:
            events.pop(0)
        if not events:
            # Don't let the per-key dict grow forever with stale, empty
            # entries for keys (users) that are no longer active.
            del self._events[key]
        return events

    def peek(self, key: object = None) -> bool:
        """Return whether `key` is currently within the limit, without
        recording an event. Useful to check multiple limiters before
        committing to any of them."""
        return len(self._prune(key)) < self.max_events

    def allow(self, key: object = None) -> bool:
        """Record an event for `key` and return whether it's within the
        limit. Rejected events are not recorded, so a caller retrying later
        (once inside the window again) can succeed."""
        events = self._prune(key)
        if len(events) >= self.max_events:
            return False
        events.append(self._now())
        self._events[key] = events
        return True
