"""python -m unittest discover tests   (the code-check tests need Node.js; they are skipped without it)

The harness runs against the real game engine and catalog, a small stand-in for serve.py's scene hub, and the fake
backend, so no test calls Claude.
"""

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from game.catalog import Catalog
from game.engine import GameEngine
from game.events import EventHub
from game.spell_request import Status
from harness import Harness, backends, codecheck
from harness.library import Library
from harness.loader import HarnessFiles, parse_md

ROOT = Path(__file__).resolve().parent.parent
HAS_NODE = shutil.which("node") is not None


def ing(kind, block_id):
    return {"type": "ingredient", "kind": kind, "id": block_id}


def text(t):
    return {"type": "text", "text": t}


class FakeHub:
    """Just what the harness uses of serve.py's Hub: the edit log, the headset, its clock and scene."""

    def __init__(self, headset=True):
        self.lock = threading.RLock()
        self.ops, self.seq = [], 0
        self.pose = {"t": 10.0, "received": time.time()}
        self.scene = {"objects": [{"name": "orange-cube"}, {"name": "lamp"}]}
        self.on_game_event = None
        self._headset = headset
        self.patch_ok = True

    def headset(self):
        return object() if self._headset else None

    def chat_scene(self, fallback):
        return self.scene

    def append(self, op, by, rid=None):
        with self.lock:
            self.seq += 1
            op = {"seq": self.seq, **op, "by": by}
            if op["kind"] == "patch":
                self.ops = [o for o in self.ops if not (o["kind"] == "patch" and o["id"] == op["id"])]
            if not (op.get("transient") and op.get("code") is None):
                self.ops.append(op)
            return op

    def edit(self, op, by, timeout):
        op = self.append(op, by)
        return {"seq": op["seq"], "applied": {"ok": self.patch_ok} if op["kind"] == "patch" else {"done": []}}

    def patches(self):
        return {o["id"]: o["code"] for o in self.ops if o["kind"] == "patch"}


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        lib_root = self.tmp / "assets"
        shutil.copytree(ROOT / "assets", lib_root, ignore=shutil.ignore_patterns("generated", "models"))
        self.catalog = Catalog.load()
        self.hub = FakeHub()
        self.harness = Harness(self.hub, self.catalog, library=Library(lib_root))
        self.harness.files.settings["backend"] = "fake"
        self.engine = GameEngine(self.catalog, EventHub(), llm_forge=self.harness.forge)
        self.calls = []
        backends.FAKE = None
        self.members = iter(self.engine.join(f"p{i}") for i in range(100))

    def tearDown(self):
        backends.FAKE = None
        self.harness.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def answer_with(self, fn):
        def fake(request):
            self.calls.append(request)
            return fn(request)
        backends.FAKE = fake

    def cast(self, *parts):
        request = self.engine.submit(next(self.members), list(parts))
        self.engine.dispatch_next(timeout=1)
        return request

    def wait(self, request, statuses=(Status.READY, Status.SENT, Status.FAILED, Status.QUEUED), timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if request.status in statuses and request.status is not Status.FORGING:
                return request
            time.sleep(0.02)
        self.fail(f"{request.id} stayed {request.status}")


def spell_answer(**over):
    answer = {
        "name": "Splitting Fireball", "incantation": "Flammtria", "object": "shape:sphere", "color": "#ff6a1f",
        "size": 0.35, "glow": True, "motion": "zigzag", "speed": 2.2, "power": 2,
        "behaviors": [{"id": "split", "params_json": "{\"count\": 3}"}],
    }
    answer.update(over)
    return answer


class LoaderTest(unittest.TestCase):
    def test_frontmatter_values(self):
        meta, body = parse_md("---\nslots: 4\nok: true\nmodel: claude-haiku-5-5\nsources: [chat, host]\n---\nHello")
        self.assertEqual(meta, {"slots": 4, "ok": True, "model": "claude-haiku-5-5", "sources": ["chat", "host"]})
        self.assertEqual(body, "Hello")

    def test_files(self):
        files = HarnessFiles()
        self.assertEqual(sorted(files.workers), ["scene-editor", "spell-adapt", "spell-combine", "spell-new"])
        self.assertIn("SPEED", files.policy)
        self.assertEqual(files.tags["urgent"].meta["weight"], 100)
        self.assertEqual([t.name for t in files.command_tools()], ["add_object", "remove_object", "update_object"])
        catalog = Catalog.load()
        schema = files.output_schema("forge_spell_new", catalog)
        self.assertEqual(schema["properties"]["motion"]["enum"], sorted(catalog.motions))
        self.assertIn("new_object", schema["properties"])
        self.assertNotIn("new_object", files.output_schema("forge_spell", catalog)["properties"])
        for worker in files.workers.values():  # every worker names an answer format that exists
            self.assertTrue(worker.meta["output"] == "edit_scene" or worker.meta["output"] in files.tools, worker.name)


@unittest.skipUnless(HAS_NODE, "needs Node.js")
class CodeCheckTest(unittest.TestCase):
    def test_prebuilt_library_passes(self):
        for item in json.loads((ROOT / "assets" / "index.json").read_text())["items"]:
            if item["type"] in ("object", "behavior"):
                ok, result = codecheck.check((ROOT / "assets" / item["file"]).read_text(), item["type"])
                self.assertTrue(ok, f"{item['id']}: {result}")

    def test_refuses_escapes(self):
        cases = {
            "window.alert(1)": "not available",
            "const f = spell.object.constructor;": "not allowed",
            "while (true) {}": "for loop",
            "this.x = 1": "this",
            "spell.onFrame(async () => {})": "async",
            "const p = spell.object.parent;": "not allowed",
            "new THREE.FileLoader()": "not allowed",
            "}); fetch('x'); (function () {": "closes the function",
            "const __k = 1;": "reserved",
        }
        for code, expected in cases.items():
            ok, errors = codecheck.check(code, "behavior")
            self.assertFalse(ok, code)
            self.assertIn(expected, " ".join(errors), code)

    def test_guards(self):
        ok, code = codecheck.check("const k = 'a'; spell[k] = 1; for (let i = 0; i < 3; i++) spell.scale *= 1;", "behavior")
        self.assertTrue(ok)
        self.assertIn("spell[__k(k)]", code)
        self.assertIn("{__loop();", code)

    def test_object_must_return(self):
        ok, errors = codecheck.check("const g = new THREE.Group();", "object")
        self.assertFalse(ok)


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(ROOT / "assets", self.tmp / "assets", ignore=shutil.ignore_patterns("generated", "models"))
        self.lib = Library(self.tmp / "assets")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_search(self):
        found = [i["id"] for _, i in self.lib.search("a lightning bolt that splits in three")]
        self.assertEqual(found[0], "bolt")
        self.assertIn("split", found)
        self.assertEqual(self.lib.search("a teapot"), [])

    def test_lifecycle(self):
        code = "spell.onFrame((age) => { spell.scale *= 1; });"
        first = self.lib.add_code("behavior", "Steady!", code, "Keeps\nsteady", ["calm"], {})
        self.assertEqual(self.lib.add_code("behavior", "other", code, "x", [], {}), first)  # same code, same item
        self.assertTrue(first.startswith("steady-"))
        self.assertEqual(self.lib.get(first)["status"], "candidate")
        self.assertNotIn(first, [i["id"] for _, i in self.lib.search("calm steady")])  # candidates aren't offered
        self.lib.used([first])
        self.assertEqual(self.lib.get(first)["status"], "verified")
        self.assertIn(first, [i["id"] for _, i in self.lib.search("calm steady")])
        self.lib.failed(first, "boom")
        self.assertEqual(self.lib.get(first)["status"], "quarantined")
        self.lib.failed("spin", "boom")  # prebuilt items are never quarantined
        self.assertEqual(self.lib.get("spin")["status"], "prebuilt")
        reloaded = Library(self.tmp / "assets")  # generated items survive a restart
        self.assertEqual(reloaded.get(first)["status"], "quarantined")


def worker_model(name):
    """The model a worker file asks for, so these tests check which worker ran without pinning model names."""
    path = Path(__file__).resolve().parent.parent / "harness" / "workers" / f"{name}.md"
    return parse_md(path.read_text(encoding="utf-8"))[0]["model"]


class ForgeTest(HarnessTest):
    def test_remix_uses_adapt_and_fast_model(self):
        self.answer_with(lambda r: spell_answer())
        request = self.wait(self.cast(ing("asset", "fireball"), text("that splits into three")))
        self.assertEqual(request.status, Status.READY)
        self.assertEqual(request.spell["harness"]["step"], "adapt")
        self.assertEqual(self.calls[0]["model"], worker_model("spell-adapt"))
        self.assertEqual(request.spell["behaviors"][0]["id"], "split")
        self.assertEqual(request.spell["behaviors"][0]["params"]["count"], 3)
        self.assertIn("SPEED", self.calls[0]["system"])  # the policy comes first
        self.assertIn("Fire:", self.calls[0]["prompt"])  # the fireball's tags bring the fire notes

    def test_same_recipe_again_needs_no_model_call(self):
        self.answer_with(lambda r: spell_answer())
        self.wait(self.cast(ing("asset", "fireball"), text("that splits into three")))
        again = self.wait(self.cast(ing("asset", "fireball"), text("That splits into THREE!")))
        self.assertEqual(again.status, Status.READY)
        self.assertEqual(again.spell["harness"]["step"], "exact")
        self.assertEqual(len(self.calls), 1)

    def test_novel_with_library_match_combines(self):
        self.answer_with(lambda r: spell_answer(object="bolt", behaviors=[]))
        request = self.wait(self.cast(text("a lightning bolt")))
        self.assertEqual(request.spell["harness"]["step"], "combine")
        self.assertEqual(request.spell["asset"]["object"]["id"], "bolt")
        self.assertEqual(request.spell["asset"]["shape"], "cylinder")  # the bolt's fallback shape
        self.assertIn("\"bolt\"", self.calls[0]["prompt"])

    @unittest.skipUnless(HAS_NODE, "needs Node.js")
    def test_novel_without_match_writes_new_code(self):
        new_object = {
            "id": "teapot", "description": "A round teapot with a spout.", "tags": ["teapot", "kitchen"],
            "params_json": "{}", "fallback_shape": "sphere",
            "code": "const g = new THREE.Group(); g.add(api.makeShape('sphere', api.material({ color: params.color }))); return g;",
        }
        self.answer_with(lambda r: spell_answer(object="teapot", behaviors=[], new_object=new_object))
        request = self.wait(self.cast(text("a screaming teapot")))
        self.assertEqual(request.spell["harness"]["step"], "new")
        self.assertEqual(self.calls[0]["model"], worker_model("spell-new"))
        self.assertIn("Spell code API", self.calls[0]["system"])
        stored = request.spell["asset"]["object"]["id"]
        self.assertTrue(stored.startswith("teapot-"))
        self.assertEqual(self.harness.library.get(stored)["status"], "candidate")

    @unittest.skipUnless(HAS_NODE, "needs Node.js")
    def test_bad_new_code_is_repaired_once_then_dropped(self):
        bad = {"id": "evil", "description": "x", "tags": [], "params_json": "{}", "fallback_shape": "cone",
               "code": "window.location = 'x'; return new THREE.Group();"}
        self.answer_with(lambda r: spell_answer(object="evil", behaviors=[], new_object=bad))
        request = self.wait(self.cast(text("a cursed weathervane")))
        self.assertEqual(len(self.calls), 2)  # the answer, then one repair
        self.assertIn("failed the code check", self.calls[1]["prompt"])
        self.assertEqual(request.status, Status.READY)  # it still flies, as the fallback shape
        self.assertEqual(request.spell["asset"]["shape"], "cone")
        self.assertNotIn("object", request.spell["asset"])

    def test_backend_error_nacks_then_last_attempt_falls_back(self):
        def fail(r):
            raise backends.BackendError("boom")
        self.answer_with(fail)
        request = self.cast(ing("asset", "ice-shard"), text("but purple"))
        self.wait(request, statuses=(Status.QUEUED,))  # first attempt nacked: back in the queue
        self.engine.dispatch_next(timeout=1)
        self.wait(request)
        self.assertEqual(request.status, Status.READY)
        self.assertEqual(request.spell["harness"]["step"], "fallback")
        self.assertEqual(request.spell["asset"]["id"], "ice-shard")  # its own block

    def test_usage_limit_pauses_and_falls_back(self):
        def limit(r):
            raise backends.UsageLimitError("usage limit reached", pause_s=60)
        self.answer_with(limit)
        request = self.wait(self.cast(text("a storm of sparks")))
        self.assertEqual(request.spell["harness"]["step"], "fallback")
        self.assertEqual(self.harness.forge.capacity, 0)
        self.assertFalse(self.harness.forge.online)

    def test_bad_values_are_fixed_without_a_call(self):
        self.answer_with(lambda r: spell_answer(color="red", motion="teleport", incantation="Aegis", size=9,
                                                behaviors=[{"id": "nope", "params_json": "{"}]))
        request = self.wait(self.cast(ing("asset", "fireball"), text("weird")))
        self.assertEqual(len(self.calls), 1)
        spell = request.spell
        self.assertEqual(spell["asset"]["color"], "#cccccc")
        self.assertEqual(spell["motion"]["id"], self.catalog.default_motion)
        self.assertNotEqual(spell["incantation"].lower(), "aegis")
        self.assertEqual(spell["asset"]["size"], 1.2)
        self.assertEqual(spell["behaviors"], [])


class BridgeTest(HarnessTest):
    def setUp(self):
        super().setUp()
        self.answer_with(lambda r: spell_answer())
        self.bridge = self.harness.bridge = __import__("harness.bridge", fromlist=["CastBridge"]).CastBridge(
            self.harness, self.hub, self.engine)
        self.engine.start_round()

    def test_cast_patch_spawned_and_removed(self):
        request = self.wait(self.cast(ing("asset", "fireball"), text("that splits into three")), statuses=(Status.SENT,))
        self.bridge.tick()
        for _ in range(100):
            if request.status is Status.CAST:
                break
            time.sleep(0.02)
        self.assertEqual(request.status, Status.CAST)  # the patch "ran", so the bridge acked "spawned"
        patches = self.hub.patches()
        self.assertIn("spell-runtime", patches)
        self.assertNotIn("/*RULES*/", patches["spell-runtime"])
        code = patches[f"spell-{request.id}"]
        self.assertIn("runtime.cast(ctx", code)
        self.assertIn("\"split\"", code)
        self.assertEqual(self.harness.library.get("split")["uses"], 1)

        self.hub.on_game_event(None, {"event": "outcome", "spell_id": request.id, "outcome": "catch"})
        self.assertEqual(request.status, Status.CAUGHT)
        self.bridge.tick()
        self.assertNotIn(f"spell-{request.id}", self.hub.patches())  # removed, and no tombstone left behind

    def test_failed_library_code_is_blamed_and_spell_cast_plain(self):
        lib = self.harness.library
        code = "spell.onFrame(() => {});"
        item = lib.add_code("behavior", "shaky", code, "x", [], {})
        self.answer_with(lambda r: spell_answer(behaviors=[]))
        request = self.wait(self.cast(ing("asset", "fireball"), text("shaky")), statuses=(Status.SENT,))
        request.spell["behaviors"] = [{"id": item, "params": {}}]  # as if the spell used it
        self.hub.patch_ok = False
        self.bridge.tick()
        time.sleep(0.3)
        self.assertEqual(lib.get(item)["status"], "quarantined")

    def test_code_error_from_headset_quarantines(self):
        item = self.harness.library.add_code("behavior", "buggy", "spell.onFrame(() => {});", "x", [], {})
        self.hub.on_game_event(None, {"event": "spell-error", "spell_id": "s1", "item": item, "error": "TypeError"})
        self.assertEqual(self.harness.library.get(item)["status"], "quarantined")

    def test_no_headset_no_cast(self):
        self.hub._headset = False
        request = self.wait(self.cast(ing("asset", "fireball"), text("later")), statuses=(Status.SENT,))
        self.bridge.tick()
        time.sleep(0.1)
        self.assertNotIn(f"spell-{request.id}", self.hub.patches())


class SceneTest(HarnessTest):
    def setUp(self):
        super().setUp()
        self.harness.scene.start()

    def test_chat_request_commits_commands(self):
        self.answer_with(lambda r: {"text": "Added a sphere.", "commands": [
            {"tool": "add_object", "name": "blue-sphere", "shape": "sphere", "color": "#3366ff", "size": 0.3,
             "position": [0, 1.5, -1], "animation": "float"},
            {"tool": "launch_rocket", "name": "x"},  # not a command: dropped
        ]})
        job = self.harness.scene.wait(self.harness.scene.submit("put a blue sphere in front of me", source="chat"), 5)
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result["commands"][0]["name"], "add_object")
        self.assertEqual(len(job.result["commands"]), 1)
        self.assertEqual(self.hub.ops[-1]["commands"][0]["input"]["name"], "blue-sphere")
        self.assertEqual(self.calls[0]["model"], worker_model("scene-editor"))

    def test_conflict_regenerates(self):
        def answer(r):
            if len(self.calls) == 1:  # while the first answer is being made, someone else edits the lamp
                self.hub.append({"kind": "commands", "commands": [{"name": "update_object", "input": {"name": "lamp", "color": "#ff0000"}}]}, by="other")
            return {"text": "ok", "commands": [{"tool": "update_object", "name": "lamp", "glow": True}]}
        self.answer_with(answer)
        job = self.harness.scene.wait(self.harness.scene.submit("make the lamp glow"), 5)
        self.assertEqual(job.status, "done")
        self.assertEqual(job.attempts, 2)

    def test_tags_priority_and_sources(self):
        job = self.harness.scene.submit("x", tags=["urgent", "cosmetic", "made-up"], source="chat")
        self.assertEqual(job.tags, ["urgent", "cosmetic"])
        self.assertEqual(job.weight, 90)
        audience = self.harness.scene.submit("x", tags=["urgent"], source="audience")
        self.assertEqual(audience.tags, [])  # urgent is only for chat, host and api

    def test_locks_from_mentioned_names(self):
        job = self.harness.scene.submit("make the orange cube spin and dim the lamp")
        self.assertEqual(job.locks, {"orange-cube", "lamp"})


if __name__ == "__main__":
    unittest.main()
