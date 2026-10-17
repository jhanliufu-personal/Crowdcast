"""Thread-safe priority queue of spell requests: by tier (PRESET, REMIX, NOVEL), then first come first served."""

import heapq
import itertools
import threading

from .spell_request import Tier


class RequestQueue:
    def __init__(self):
        self._heap = []  # (tier, seq, request); seq is unique, so requests are never compared
        self._seq = itertools.count()
        self._cond = threading.Condition()

    def push(self, request):
        with self._cond:
            heapq.heappush(self._heap, (int(request.tier), next(self._seq), request))
            self._cond.notify_all()

    def pop(self, can_take, timeout=None):
        """Remove and return the front request once can_take(front) is true, or None after timeout.

        The front is only ever skipped by waiting, never reordered: if an LLM request is at the
        front with no LLM capacity, a PRESET pushed later still jumps ahead (it sorts first).
        can_take runs under this queue's lock, so it must not take the engine's lock.
        """
        with self._cond:
            if not self._cond.wait_for(lambda: self._heap and can_take(self._heap[0][2]), timeout):
                return None
            return heapq.heappop(self._heap)[2]

    def wake(self):
        """Re-check can_take, e.g. after LLM capacity frees up."""
        with self._cond:
            self._cond.notify_all()

    def drain(self):
        """Remove and return everything, in queue order."""
        with self._cond:
            items = self.snapshot()  # Condition's lock is reentrant
            self._heap.clear()
            return items

    def snapshot(self):
        """Queued requests in the order they will be served."""
        with self._cond:
            return [entry[2] for entry in sorted(self._heap)]

    def ahead_of(self, request):
        """How many queued requests will be served before this one, or None if it isn't queued."""
        with self._cond:
            key = next((entry[:2] for entry in self._heap if entry[2] is request), None)
            return None if key is None else sum(entry[:2] < key for entry in self._heap)

    def counts(self):
        with self._cond:
            counts = {t.name.lower(): 0 for t in Tier}
            for tier, _, _ in self._heap:
                counts[Tier(tier).name.lower()] += 1
            return counts

    def __len__(self):
        with self._cond:
            return len(self._heap)
