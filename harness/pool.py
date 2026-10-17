"""The shared model-call slots, the usage-limit pause, and the numbers GET /api/harness shows."""

import collections
import threading
import time


class Pool:
    def __init__(self, files):
        self.files = files
        self._lock = threading.Lock()
        self.spells_running = 0  # claimed by the engine (it never claims more than forge capacity)
        self.scene_running = 0
        self.paused_until = 0.0
        self.pause_reason = None
        self.running = {}  # job id -> {kind, step, worker, started}
        self.recent = collections.deque(maxlen=50)
        self.counts = collections.Counter()

    # --- capacity ---

    @property
    def slots(self):
        return int(self.files.fresh().setting("slots", 4))

    def paused(self):
        return time.time() < self.paused_until

    def pause(self, seconds, reason):
        with self._lock:
            self.paused_until = max(self.paused_until, time.time() + seconds)
            self.pause_reason = reason
        self.counts["pauses"] += 1

    def spell_capacity(self):
        """How many spell jobs may run at once: what scene edits don't use, or 0 while paused."""
        if self.paused():
            return 0
        return max(0, self.slots - self.scene_running)

    def try_scene_slot(self):
        with self._lock:
            limit = min(int(self.files.setting("scene_max_slots", 1)), self.slots - self.spells_running)
            if self.paused() or self.scene_running >= limit:
                return False
            self.scene_running += 1
            return True

    def release_scene_slot(self):
        with self._lock:
            self.scene_running -= 1

    # --- bookkeeping ---

    def start(self, job_id, kind, **info):
        with self._lock:
            if kind == "spell":
                self.spells_running += 1
            self.running[job_id] = {"kind": kind, "started": time.time(), **info}

    def update(self, job_id, **info):
        with self._lock:
            if job_id in self.running:
                self.running[job_id].update(info)

    def finish(self, job_id, outcome, **info):
        with self._lock:
            entry = self.running.pop(job_id, {"kind": "?", "started": time.time()})
            if entry["kind"] == "spell":
                self.spells_running -= 1
            record = {
                "id": job_id, **entry, **info, "outcome": outcome,
                "ms": round((time.time() - entry["started"]) * 1000), "at": time.strftime("%H:%M:%S"),
            }
            record.pop("started", None)
            self.recent.appendleft(record)
            self.counts[f"{entry['kind']}:{outcome}"] += 1
            if info.get("step"):
                self.counts[f"step:{info['step']}"] += 1

    def status(self):
        now = time.time()
        with self._lock:
            running = [{"id": k, **{x: y for x, y in v.items() if x != "started"}, "s": round(now - v["started"], 1)}
                       for k, v in self.running.items()]
            steps = {k[5:]: v for k, v in self.counts.items() if k.startswith("step:")}
            done = sum(steps.values())
            return {
                "slots": self.slots,
                "spell_capacity": self.spell_capacity(),
                "paused": self.paused(),
                "pause_reason": self.pause_reason if self.paused() else None,
                "pause_left_s": round(max(0, self.paused_until - now)),
                "running": running,
                "steps": steps,
                # Share of finished spells that needed no new code (exact reuse, adapt, combine, fallback).
                "reuse_rate": round(1 - steps.get("new", 0) / done, 2) if done else None,
                "counts": {k: v for k, v in self.counts.items() if not k.startswith("step:")},
                "recent": list(self.recent)[:20],
            }
