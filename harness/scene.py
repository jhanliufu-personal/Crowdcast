"""Scene edits through the harness: a priority queue, name locks, and a conflict check before each commit.

    submit(text, tags, source) ──▶ queue (priority: tag weights + 1 per second waited; dropped after scene_ttl_s)
        ──▶ a free scene slot, and no running job that names the same objects
        ──▶ the scene-editor worker reads the scene (and the log's seq) and answers with commands
        ──▶ did another edit change one of the same objects since that seq? yes: make the commands again
        ──▶ no: append the commands to the edit log (every page applies them)
"""

import itertools
import json
import logging
import re
import threading
import time

from . import backends

log = logging.getLogger("harness.scene")
_ids = itertools.count(1)


class SceneJob:
    def __init__(self, text, tags, source, scene_fallback):
        self.id = f"e{next(_ids)}"
        self.text, self.tags, self.source, self.scene_fallback = text, tags, source, scene_fallback
        self.created = time.time()
        self.weight = 0
        self.locks = set()
        self.status = "queued"
        self.attempts = 0
        self.result = None
        self.done = threading.Event()

    def priority(self, now):
        return self.weight + (now - self.created)

    def public(self):
        out = {"id": self.id, "status": self.status, "text": self.text, "tags": self.tags, "source": self.source,
               "attempts": self.attempts, "waited_s": round(time.time() - self.created, 1)}
        if self.result is not None:
            out["result"] = self.result
        return out


class SceneQueue:
    def __init__(self, harness, hub):
        self.h, self.hub = harness, hub
        self.queue = []
        self.running = {}
        self.finished = {}
        self._cond = threading.Condition()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._dispatch, name="scene-dispatch", daemon=True).start()

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

    def submit(self, text, tags=(), source="api", scene_fallback=None):
        files = self.h.files.fresh()
        job = SceneJob(str(text)[:500], [], source, scene_fallback or {})
        for tag in tags or []:
            doc = files.tags.get(str(tag))
            if not doc or doc.meta.get("applies_to") not in ("scene", "both"):
                log.info("scene request %s: unknown tag %r ignored", job.id, tag)
                continue
            allowed = doc.meta.get("sources")
            if allowed and source not in allowed:
                log.info("scene request %s: tag %r not allowed from %s", job.id, tag, source)
                continue
            job.tags.append(doc.name)
            job.weight += float(doc.meta.get("weight", 0))
        job.locks = self._mentioned(job.text)
        with self._cond:
            self.queue.append(job)
            self._cond.notify_all()
        return job

    def wait(self, job, timeout):
        job.done.wait(timeout)
        return job

    def get(self, job_id):
        with self._cond:
            for job in self.queue + list(self.running.values()) + list(self.finished.values()):
                if job.id == job_id:
                    return job
        return None

    # --- scheduling ---

    def _dispatch(self):
        while not self._stop.is_set():
            with self._cond:
                job = self._next()
                if job is None:
                    self._cond.wait(0.25)
                    continue
            threading.Thread(target=self._run, args=(job,), name=f"scene-{job.id}", daemon=True).start()

    def _next(self):
        """Under the lock: drop expired jobs, then take the best job that has a slot and no lock conflict."""
        now = time.time()
        ttl = float(self.h.files.setting("scene_ttl_s", 60))
        for job in [j for j in self.queue if now - j.created > ttl]:
            self.queue.remove(job)
            self._finish(job, "expired", {"error": f"waited more than {ttl:.0f} s"})
        locked = set().union(*(j.locks for j in self.running.values())) if self.running else set()
        for job in sorted(self.queue, key=lambda j: -j.priority(now)):
            if job.locks & locked or ("*" in locked and job.locks) or ("*" in job.locks and self.running):
                continue
            if not self.h.pool.try_scene_slot():
                return None
            self.queue.remove(job)
            self.running[job.id] = job
            return job
        return None

    def _finish(self, job, status, result):
        job.status, job.result = status, result
        self.finished[job.id] = job
        if len(self.finished) > 100:
            self.finished.pop(next(iter(self.finished)))
        job.done.set()

    # --- one job ---

    def _run(self, job):
        files = self.h.files.fresh()
        worker = files.worker("scene-editor")
        self.h.pool.start(job.id, "scene", worker="scene-editor", model=worker.meta.get("model"))
        status, result = "failed", None
        try:
            max_attempts = int(files.setting("scene_max_attempts", 3))
            timeout_s = float(files.setting("scene_timeout_s", 90))
            while job.attempts < max_attempts:
                job.attempts += 1
                job.status = "running"
                with self.hub.lock:
                    base_seq = self.hub.seq
                scene = self.hub.chat_scene(job.scene_fallback)
                answer, info = self._call(files, worker, job, scene, timeout_s)
                commands = self._commands(answer, files)
                touched = names_touched(commands)
                if self._conflicts(base_seq, touched):
                    log.info("scene %s: conflict on %s, attempt %d", job.id, sorted(touched), job.attempts)
                    continue
                if commands:
                    self.hub.append({"kind": "commands", "commands": commands}, by=f"harness:{job.source}")
                status = "done"
                result = {"commands": commands, "text": str(answer.get("text", "")), "model": info["model"], "ms": info["ms"]}
                break
            else:
                result = {"error": f"other edits kept changing the same objects ({max_attempts} attempts)"}
        except backends.UsageLimitError as err:
            self.h.pool.pause(err.pause_s or files.setting("usage_limit_pause_s", 300), str(err)[:120])
            result = {"error": f"usage limit reached: {err}"}
        except backends.BackendError as err:
            result = {"error": str(err)}
        except Exception as err:
            log.exception("scene job %s failed", job.id)
            result = {"error": f"{type(err).__name__}: {err}"}
        finally:
            self.h.pool.release_scene_slot()
            self.h.pool.finish(job.id, status, error=(result or {}).get("error"), attempts=job.attempts)
            with self._cond:
                self.running.pop(job.id, None)
                self._finish(job, status, result)
                self._cond.notify_all()

    def _call(self, files, worker, job, scene, timeout_s):
        system = "\n\n".join([files.policy, f"# Your task\n\n{worker.body}", f"# Commands\n\n{files.output_doc('edit_scene')}"])
        notes = [files.tags[t].body for t in job.tags if files.tags[t].body]
        prompt = "\n".join(
            ["## Scene state", json.dumps(scene)]
            + (["\n## Notes for this request's tags"] + [f"- {n}" for n in notes] if notes else [])
            + ["\n## Request", job.text]
        )
        return backends.call(
            self.h.backend, system=system, prompt=prompt, schema=files.output_schema("edit_scene", self.h.catalog),
            model=worker.meta.get("model", "claude-sonnet-5-5"), effort=worker.meta.get("effort", "low"),
            max_tokens=int(worker.meta.get("max_tokens", 4000)), timeout=timeout_s,
        )

    def _commands(self, answer, files):
        """The answer's commands in the edit log's shape, keeping only known tools and their own fields."""
        out = []
        for c in answer.get("commands") or []:
            if not isinstance(c, dict):
                continue
            schema = files.command_schema(c.get("tool"))
            if not schema:
                continue
            fields = {k: v for k, v in c.items() if k in schema["properties"] and v is not None}
            if all(k in fields for k in schema.get("required", [])):
                out.append({"name": c["tool"], "input": fields})
        return out

    def _conflicts(self, base_seq, touched):
        if not touched:
            return False
        with self.hub.lock:
            later = [o for o in self.hub.ops if o["seq"] > base_seq and o["kind"] == "commands"]
        for op in later:
            other = names_touched(op["commands"])
            if "*" in other or "*" in touched or other & touched:
                return True
        return False

    def _mentioned(self, text):
        """Names of existing objects that the request text mentions (or "*" for everything)."""
        scene = self.hub.chat_scene({}) or {}
        lowered = text.lower()
        names = set()
        for obj in scene.get("objects", []):
            name = str(obj.get("name", ""))
            if name and (name.lower() in lowered or name.lower().replace("-", " ") in lowered):
                names.add(name)
        if re.search(r"\b(everything|all objects|every object|clear the room)\b", lowered):
            names.add("*")
        return names

    def status(self):
        with self._cond:
            return {
                "queued": [j.public() for j in sorted(self.queue, key=lambda j: -j.priority(time.time()))],
                "running": [j.public() for j in self.running.values()],
                "recent": [j.public() for j in list(self.finished.values())[-10:]][::-1],
            }


def names_touched(commands):
    """The object names a list of commands adds, changes or removes."""
    return {str(c.get("input", {}).get("name")) for c in commands if c.get("input", {}).get("name")}

