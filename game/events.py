"""Fan-out of game events to connected clients (each one a Server-Sent Events stream)."""

import json
import queue
import threading

ROLES = ("audience", "headset", "spectator", "host")


class Subscription:
    def __init__(self, role, member_id=None):
        self.role = role
        self.member_id = member_id
        self.closed = False
        self._q = queue.Queue(maxsize=256)

    def get(self, timeout):
        """Next (event, json_payload); raises queue.Empty on timeout."""
        return self._q.get(timeout=timeout)


class EventHub:
    def __init__(self):
        self._subs = set()
        self._lock = threading.Lock()

    def subscribe(self, role, member_id=None):
        sub = Subscription(role, member_id)
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub):
        sub.closed = True
        with self._lock:
            self._subs.discard(sub)

    def publish(self, event, data, roles=None, member_id=None):
        """Send to every subscriber in roles (default all), or only to one audience member's streams.

        Never blocks: a subscriber that has fallen 256 events behind is dropped, and its client
        reconnects and starts again from a fresh snapshot.
        """
        payload = json.dumps(data)  # serialize now; the data may change after this returns
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            if (roles and sub.role not in roles) or (member_id and sub.member_id != member_id):
                continue
            try:
                sub._q.put_nowait((event, payload))
            except queue.Full:
                self.unsubscribe(sub)

    def online_members(self):
        """Audience members with at least one open stream."""
        with self._lock:
            return {s.member_id for s in self._subs if s.member_id}

    def counts(self):
        with self._lock:
            return {role: sum(s.role == role for s in self._subs) for role in ROLES}
