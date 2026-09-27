import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_THIS_DIR))

from ratelimit import RateLimiter


def test_rate_limiter_allows_then_blocks_then_recovers_after_window():
    clock = {'t': 0.0}

    def now():
        return clock['t']

    limiter = RateLimiter(max_events=3, window=10, now=now)

    assert limiter.allow('alice') is True
    assert limiter.allow('alice') is True
    assert limiter.allow('alice') is True
    # 4th event within the window is over the limit.
    assert limiter.allow('alice') is False
    assert limiter.allow('alice') is False

    # Still blocked just before the window fully elapses.
    clock['t'] = 9.9
    assert limiter.allow('alice') is False

    # Once the oldest events fall out of the window, new events are allowed.
    clock['t'] = 10.1
    assert limiter.allow('alice') is True


def test_rate_limiter_keys_are_independent():
    clock = {'t': 0.0}

    def now():
        return clock['t']

    limiter = RateLimiter(max_events=1, window=10, now=now)

    assert limiter.allow('alice') is True
    assert limiter.allow('bob') is True
    assert limiter.allow('alice') is False
    assert limiter.allow('bob') is False


def test_rate_limiter_global_key_none():
    clock = {'t': 0.0}

    def now():
        return clock['t']

    limiter = RateLimiter(max_events=2, window=5, now=now)

    assert limiter.allow() is True
    assert limiter.allow() is True
    assert limiter.allow() is False


def test_rate_limiter_peek_does_not_record():
    clock = {'t': 0.0}

    def now():
        return clock['t']

    limiter = RateLimiter(max_events=1, window=10, now=now)

    # peek() can be called repeatedly without ever consuming the slot.
    assert limiter.peek('alice') is True
    assert limiter.peek('alice') is True
    assert limiter.peek('alice') is True

    assert limiter.allow('alice') is True
    assert limiter.peek('alice') is False
    assert limiter.allow('alice') is False


def test_rate_limiter_prunes_empty_keys():
    clock = {'t': 0.0}

    def now():
        return clock['t']

    limiter = RateLimiter(max_events=2, window=5, now=now)

    assert limiter.allow('alice') is True
    assert 'alice' in limiter._events

    # Once the window has fully elapsed, the next prune (via peek or
    # allow) should drop the now-empty per-key entry entirely rather than
    # letting the dict grow forever.
    clock['t'] = 10
    limiter.peek('alice')
    assert 'alice' not in limiter._events
