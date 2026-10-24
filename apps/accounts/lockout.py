"""Per-username login lockout, complementing the per-IP DRF login throttle."""

from django.conf import settings
from django.core.cache import cache


def _key(username):
    return f"login-fail:{(username or '').strip().lower()[:150]}"


def is_locked(username):
    return cache.get(_key(username), 0) >= settings.LOGIN_MAX_FAILURES


def register_failure(username):
    key = _key(username)
    count = cache.get(key, 0) + 1
    cache.set(key, count, timeout=settings.LOGIN_LOCKOUT_MINUTES * 60)
    return count


def reset(username):
    cache.delete(_key(username))
