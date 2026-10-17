"""The cast bridge: turns each spell the engine casts into a three.js patch in the scene hub's edit log.

    engine casts spell s7 ──▶ patch "spell-s7" (transient: never saved to scenes/live.json) ──▶ every page runs it
    the headset reports the patch ran ──▶ engine.report("spawned", "s7"), and the library items count as used
    the spell leaves the engine's in-flight list (outcome, fizzle, reset) ──▶ the patch is removed
    the headset reports an outcome or a code error (ctx.report) ──▶ engine.report(...) / library.failed(...)

The first spell also puts the spell runtime (harness/runtime/spell-runtime.js) into the log as patch
"spell-runtime". Every spell patch is small: it calls the runtime with the spell and its library code.
"""

import json
import logging
import threading
import time
from pathlib import Path

from game.engine import GameError

from . import codecheck

log = logging.getLogger("harness.bridge")
RUNTIME = Path(__file__).resolve().parent / "runtime" / "spell-runtime.js"
RUNTIME_ID = "spell-runtime"
POLL_S = 0.1
LEAD_S = 0.3  # the patch reaches the pages a little after the server sends it


class CastBridge:
    def __init__(self, harness, hub, engine):
        self.h, self.hub, self.engine = harness, hub, engine
        self.patched = {}  # spell id -> library item ids it used
        self.pending = set()  # spell ids whose patch is being applied
        self._lock = threading.Lock()
        self._stop = threading.Event()
        hub.on_game_event = self.on_event

    def start(self):
        threading.Thread(target=self._loop, name="cast-bridge", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(POLL_S):
            try:
                self.tick()
            except Exception:
                log.exception("cast bridge tick failed")

    def tick(self):
        """Patch new casts, remove patches of spells that are no longer in flight."""
        in_flight = {str(s["id"]): s for s in self.engine.state()["in_flight"]}
        with self._lock:
            gone = [sid for sid in self.patched if sid not in in_flight]
            for sid in gone:
                self.patched.pop(sid)
        for sid in gone:
            self.hub.append({"kind": "patch", "id": f"spell-{sid}", "code": None, "transient": True}, by="harness")
        if self.hub.headset() is None:
            return  # nobody to show it to: the engine keeps it as "sent" and the bridge casts it when a headset connects
        for spell in self.engine.unconfirmed_casts():
            sid = str(spell["id"])
            with self._lock:
                if sid in self.patched or sid in self.pending:
                    continue
                self.pending.add(sid)
            threading.Thread(target=self._cast, args=(spell,), name=f"cast-{sid}", daemon=True).start()

    def _cast(self, spell):
        sid = str(spell["id"])
        try:
            self.ensure_runtime()
            code, items = self.patch_code(spell)
            result = self.hub.edit({"kind": "patch", "id": f"spell-{sid}", "code": code, "transient": True}, by="harness", timeout=10)
            applied = result.get("applied") or {}
            if not applied.get("ok") and items:
                # The library code failed while setting up: blame it, and cast the spell as a plain shape instead.
                for item in items:
                    self.h.library.failed(item, applied.get("error") or result.get("note") or "patch failed")
                code, items = self.patch_code(spell, plain=True)
                result = self.hub.edit({"kind": "patch", "id": f"spell-{sid}", "code": code, "transient": True}, by="harness", timeout=10)
                applied = result.get("applied") or {}
            with self._lock:
                self.patched[sid] = items
            if applied.get("ok"):
                self.h.library.used(items + [spell.get("library", {}).get("spell")])
                self._report("spawned", sid)
            else:
                log.warning("spell %s patch did not report success: %s", sid, applied or result.get("note"))
        except Exception:
            log.exception("casting %s failed", sid)
        finally:
            with self._lock:
                self.pending.discard(sid)

    def ensure_runtime(self):
        with self.hub.lock:
            present = any(o["kind"] == "patch" and o["id"] == RUNTIME_ID and o.get("code") for o in self.hub.ops)
        if not present:
            rules = json.dumps({k: codecheck.RULES[k] for k in ("deny_members", "deny_member_pattern")})
            code = RUNTIME.read_text(encoding="utf-8").replace("/*RULES*/ { deny_members: [], deny_member_pattern: '^$' }", rules)
            self.hub.edit({"kind": "patch", "id": RUNTIME_ID, "code": code, "transient": True}, by="harness", timeout=10)

    def patch_code(self, spell, plain=False):
        """The patch for one spell, and the library items it uses. plain=True leaves the library code out."""
        lib = self.h.library
        definition = {k: v for k, v in spell.items() if k not in ("caster", "library", "harness", "notes")}
        definition["t0"] = self.headset_clock() + LEAD_S
        items, parts = [], []
        obj = (spell.get("asset") or {}).get("object")
        object_fn = "null"
        if obj and not plain:
            item = lib.get(obj["id"])
            if item and item["type"] == "model":
                definition["model"] = "/assets/" + item["file"]
                items.append(obj["id"])
            elif item:
                body = lib.code(obj["id"])
                if body:
                    object_fn = f"{{ id: {json.dumps(obj['id'])}, params: {json.dumps(obj.get('params', {}))}, fn: {wrap(body, 'object')} }}"
                    items.append(obj["id"])
        if not plain:
            for b in spell.get("behaviors") or []:
                body = lib.code(b["id"])
                if body:
                    parts.append(f"{{ id: {json.dumps(b['id'])}, params: {json.dumps(b.get('params', {}))}, fn: {wrap(body, 'behavior')} }}")
                    items.append(b["id"])
        caster = (spell.get("caster") or {}).get("name", "?")
        code = (
            f"// spell {spell['id']}: {json.dumps(spell.get('name'))} by {json.dumps(caster)}, cast by the harness\n"
            f"const runtime = ctx.scene.getObjectByName('{RUNTIME_ID}')?.userData.runtime;\n"
            f"if (!runtime) throw new Error('the {RUNTIME_ID} patch is not loaded');\n"
            f"await runtime.cast(ctx, {json.dumps(definition)}, {{\n"
            f"  object: {object_fn},\n"
            f"  behaviors: [{', '.join(parts)}],\n"
            f"}});\n"
        )
        return code, items

    def headset_clock(self):
        """The headset's clock (t in its poses) now, estimated from its latest pose."""
        pose = self.hub.pose
        if not pose or "t" not in pose:
            return float("nan")  # the runtime then starts the spell at its first frame
        return pose["t"] + (time.time() - pose["received"])

    def on_event(self, ws, msg):
        """A message from the headset page's spell runtime (ctx.report)."""
        kind = msg.get("event")
        if kind == "outcome" and msg.get("outcome") in ("hit", "catch", "shield"):
            self._report(msg["outcome"], str(msg.get("spell_id")))
        elif kind == "spell-error":
            for item in str(msg.get("item") or "").split(","):
                if item:
                    self.h.library.failed(item, msg.get("error"))
            log.warning("spell %s code error in %s: %s", msg.get("spell_id"), msg.get("item"), msg.get("error"))

    def _report(self, event, sid):
        try:
            self.engine.report(event, sid)
        except GameError as err:
            log.info("engine refused %s for %s: %s", event, sid, err)


def wrap(body, kind):
    """A checked function body as a function literal (the same shape check.mjs parsed it in)."""
    params = {"object": "THREE, api, params", "behavior": "THREE, api, spell, params"}[kind]
    return f"function ({params}, __k, __loop) {{\n'use strict';\n{body}\n}}"
