"""Database-backed run locks that work across processes and servers sharing the DB."""

import os
import socket
import uuid
from contextlib import contextmanager
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import JobLock


class LockNotAcquired(Exception):
    pass


def _holder():
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def acquire(name, ttl_minutes=60):
    now = timezone.now()
    holder = _holder()
    expires = now + timedelta(minutes=ttl_minutes)
    # Take over a stale lock atomically (conditional UPDATE).
    taken = JobLock.objects.filter(name=name, expires_at__lt=now).update(holder=holder, acquired_at=now, expires_at=expires)
    if taken:
        return holder
    try:
        with transaction.atomic():
            JobLock.objects.create(name=name, holder=holder, acquired_at=now, expires_at=expires)
        return holder
    except IntegrityError:
        raise LockNotAcquired(name)


def release(name, holder):
    JobLock.objects.filter(name=name, holder=holder).delete()


@contextmanager
def run_lock(name, ttl_minutes=60):
    holder = acquire(name, ttl_minutes)
    try:
        yield holder
    finally:
        release(name, holder)
