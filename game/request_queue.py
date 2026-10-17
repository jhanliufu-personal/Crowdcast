"""Thread-safe priority queue of spell requests with claim / ack / nack.

Order: by tier (PRESET, REMIX, NOVEL), then arrival. A request leaves the queue only when its
consumer acknowledges it:

    queued ──claim()──▶ claimed ──ack(lease)──▶ removed
                          │
                          ├── nack(lease) or lease timeout ──▶ queued again, same position
                          └── ...with no attempts left ──────▶ removed (the engine marks it failed)

claim() returns a Lease. ack/nack must present the lease from the current claim: a lease from
before a clear() (generation changed), from an earlier attempt, or already answered is rejected.
That is what makes reset safe while a slow consumer is still working.
"""

import itertools
import threading
import time
from dataclasses import dataclass

from .spell_request import Tier


@dataclass(frozen=True)
class Lease:
    request_id: str
    generation: int  # queue generation when claimed; clear() bumps it
    attempt: int     # 1 for the first claim of this request, 2 for the retry, ...
    deadline: float  # queue clock time after which the claim lapses


class _Entry:
    __slots__ = ("key", "request", "lease", "attempts")

    def __init__(self, key, request):
        self.key = key  # (tier, seq): sort order
        self.request = request
        self.lease = None
        self.attempts = 0


class RequestQueue:
    def __init__(self, lease_seconds, max_attempts=2, clock=time.monotonic):
        """lease_seconds: Tier -> seconds a claim lasts before it lapses."""
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._clock = clock
        self._entries = []  # sorted by key
        self._seq = itertools.count()
        self._generation = 0
        self._cond = threading.Condition()  # reentrant: methods may call each other under it

    # ---- producer ----------------------------------------------------------------------------

    def push(self, request):
        with self._cond:
            self._entries.append(_Entry((int(request.tier), next(self._seq)), request))
            self._entries.sort(key=lambda e: e.key)
            self._cond.notify_all()

    # ---- consumer ----------------------------------------------------------------------------

    def claim(self, can_take, timeout=None):
        """Claim the first unclaimed request once can_take(request) is true.

        Returns (request, lease), or None after timeout. Claimed requests keep their place.
        can_take runs under this queue's lock; it may call this queue's methods, but must not
        take the engine's lock.
        """
        def front():
            entry = next((e for e in self._entries if e.lease is None), None)
            return entry if entry and can_take(entry.request) else None

        with self._cond:
            if not self._cond.wait_for(front, timeout):
                return None
            entry = front()
            entry.attempts += 1
            seconds = self._lease_seconds[Tier(entry.key[0])]
            entry.lease = Lease(entry.request.id, self._generation, entry.attempts, self._clock() + seconds)
            return entry.request, entry.lease

    def ack(self, lease):
        """Served: remove the request. Returns it, or None if the lease is no longer current."""
        with self._cond:
            entry = self._current(lease)
            if entry is None:
                return None
            self._entries.remove(entry)
            self._cond.notify_all()
            return entry.request

    def nack(self, lease):
        """Not served. Returns (request, retrying), or None if the lease is no longer current.

        With attempts left the request goes back to its old position, unclaimed; otherwise it is
        removed and retrying is False.
        """
        with self._cond:
            entry = self._current(lease)
            return None if entry is None else self._release(entry)

    def expire_leases(self):
        """Treat every lapsed claim as a nack. Returns [(request, retrying), ...]."""
        with self._cond:
            now = self._clock()
            return [self._release(e) for e in list(self._entries) if e.lease and e.lease.deadline <= now]

    def is_current(self, lease):
        with self._cond:
            return self._current(lease) is not None

    def wake(self):
        """Re-check can_take, e.g. after consumer capacity freed up."""
        with self._cond:
            self._cond.notify_all()

    def clear(self):
        """Remove everything, claimed or not, and invalidate every outstanding lease."""
        with self._cond:
            requests = [e.request for e in self._entries]
            self._entries.clear()
            self._generation += 1
            self._cond.notify_all()
            return requests

    # ---- inspection --------------------------------------------------------------------------

    def snapshot(self):
        """[(request, lease or None), ...] in the order they will be served."""
        with self._cond:
            return [(e.request, e.lease) for e in self._entries]

    def ahead_of(self, request):
        """How many queued requests (claimed or not) are ahead of this one, or None if it isn't queued."""
        with self._cond:
            return next((i for i, e in enumerate(self._entries) if e.request is request), None)

    def claimed_count(self, tiers):
        with self._cond:
            return sum(e.lease is not None and Tier(e.key[0]) in tiers for e in self._entries)

    def counts(self):
        with self._cond:
            counts = {t.name.lower(): 0 for t in Tier}
            for e in self._entries:
                counts[Tier(e.key[0]).name.lower()] += 1
            return counts

    def __len__(self):
        with self._cond:
            return len(self._entries)

    # ---- internals ---------------------------------------------------------------------------

    def _current(self, lease):
        if lease.generation != self._generation:
            return None
        return next((e for e in self._entries if e.lease == lease), None)

    def _release(self, entry):
        entry.lease = None
        retrying = entry.attempts < self._max_attempts
        if not retrying:
            self._entries.remove(entry)
        self._cond.notify_all()
        return entry.request, retrying
