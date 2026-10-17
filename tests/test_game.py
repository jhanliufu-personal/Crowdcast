"""python -m unittest discover tests"""

import json
import unittest

from game.catalog import Catalog
from game.engine import ACTS, COOLDOWN_S, MAX_CRACKS, ROUND_SECONDS, SPELL_LIFETIME_S, GameEngine, GameError
from game.events import EventHub
from game.request_queue import RequestQueue
from game.spell_request import Recipe, RequestError, SpellRequest, Status, Tier, resolve_preset

CATALOG = Catalog.load()


def ing(kind, block_id):
    return {"type": "ingredient", "kind": kind, "id": block_id}


def text(t):
    return {"type": "text", "text": t}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeDispatcher:
    online = True

    def __init__(self, capacity=1):
        self.capacity = capacity
        self.jobs = []

    def has_capacity(self):
        return len(self.jobs) < self.capacity

    def submit(self, request, catalog, done):
        self.jobs.append((request, done))


def collect(hub, role="headset", member_id=None):
    sub = hub.subscribe(role, member_id)

    def drain():
        out = []
        while True:
            try:
                event, payload = sub.get(timeout=0)
            except Exception:
                return out
            out.append((event, json.loads(payload)))

    return drain


class TierTest(unittest.TestCase):
    def tier(self, *parts):
        return Recipe.parse(list(parts), CATALOG).tier()

    def test_preset(self):
        self.assertIs(self.tier(ing("spell", "ember-bolt")), Tier.PRESET)
        self.assertIs(self.tier(ing("asset", "fireball")), Tier.PRESET)
        self.assertIs(self.tier(ing("asset", "fireball"), ing("motion", "zigzag")), Tier.PRESET)
        self.assertIs(self.tier(ing("spell", "ember-bolt"), ing("motion", "spiral")), Tier.PRESET)
        self.assertIs(self.tier(ing("asset", "fireball"), text("   ")), Tier.PRESET)  # blank text is dropped

    def test_remix(self):
        self.assertIs(self.tier(ing("asset", "fireball"), text("that splits in three")), Tier.REMIX)
        self.assertIs(self.tier(ing("spell", "frost-lance"), text("but purple")), Tier.REMIX)
        self.assertIs(self.tier(ing("asset", "fireball"), ing("asset", "cursed-book")), Tier.REMIX)

    def test_novel(self):
        self.assertIs(self.tier(text("a swarm of paper birds")), Tier.NOVEL)
        self.assertIs(self.tier(ing("motion", "spiral"), text("a teapot")), Tier.NOVEL)

    def test_invalid(self):
        for parts in ([], [ing("motion", "zigzag")], [ing("asset", "nope")], [ing("bogus", "fireball")],
                      [text("x" * 121)], [{"type": "html"}], "fireball", [ing("asset", "fireball")] * 9):
            with self.subTest(parts=parts), self.assertRaises(RequestError):
                Recipe.parse(parts, CATALOG)

    def test_resolve(self):
        spell = resolve_preset(Recipe.parse([ing("spell", "ember-bolt")], CATALOG), CATALOG)
        self.assertEqual((spell["name"], spell["incantation"]), ("Ember Bolt", "Emberos"))
        spell = resolve_preset(Recipe.parse([ing("spell", "ember-bolt"), ing("motion", "zigzag")], CATALOG), CATALOG)
        self.assertEqual((spell["name"], spell["incantation"], spell["motion"]["id"]), ("Zigzagging Fireball", "Flammizzo", "zigzag"))
        spell = resolve_preset(Recipe.parse([ing("asset", "ice-shard")], CATALOG), CATALOG)
        self.assertEqual(spell["motion"]["id"], CATALOG.default_motion)


class QueueTest(unittest.TestCase):
    def test_order_and_gating(self):
        q = RequestQueue()
        reqs = {}
        for name, parts in [("novel", [text("a dragon")]), ("remix", [ing("asset", "fireball"), text("bigger")]),
                            ("preset1", [ing("asset", "fireball")]), ("preset2", [ing("spell", "ember-bolt")])]:
            recipe = Recipe.parse(parts, CATALOG)
            reqs[name] = SpellRequest("m", recipe, recipe.tier())
            q.push(reqs[name])
        self.assertEqual(q.ahead_of(reqs["novel"]), 3)
        presets_only = lambda r: r.tier is Tier.PRESET
        self.assertIs(q.pop(presets_only, 0), reqs["preset1"])
        self.assertIs(q.pop(presets_only, 0), reqs["preset2"])
        self.assertIsNone(q.pop(presets_only, 0))
        self.assertIs(q.pop(lambda r: True, 0), reqs["remix"])
        self.assertIs(q.pop(lambda r: True, 0), reqs["novel"])


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.hub = EventHub()
        self.dispatcher = FakeDispatcher()
        self.engine = GameEngine(CATALOG, self.hub, self.dispatcher, clock=self.clock)
        self.a = self.engine.join("Maya")
        self.b = self.engine.join("Theo")

    def submit(self, member, *parts):
        request = self.engine.submit(member, list(parts))
        self.clock.t += COOLDOWN_S
        return request

    def test_teams_balanced(self):
        self.assertNotEqual(self.a.team, self.b.team)

    def test_preset_waits_for_round_then_casts(self):
        headset = collect(self.hub)
        r = self.submit(self.a, ing("spell", "ember-bolt"))
        self.engine.dispatch_next()
        self.assertIs(r.status, Status.READY)
        self.assertEqual([e for e, _ in headset() if e == "spell:cast"], [])
        self.engine.start_round()
        self.assertIs(r.status, Status.CAST)
        casts = [d for e, d in headset() if e == "spell:cast"]
        self.assertEqual(casts[0]["id"], r.id)
        self.assertEqual(casts[0]["caster"]["name"], "Maya")

    def test_cooldown_and_one_pending(self):
        self.engine.submit(self.a, [ing("asset", "fireball")])
        with self.assertRaises(GameError) as err:
            self.engine.submit(self.a, [ing("asset", "fireball")])
        self.assertEqual(err.exception.status, 429)
        self.clock.t += COOLDOWN_S
        with self.assertRaises(GameError) as err:
            self.engine.submit(self.a, [ing("asset", "fireball")])
        self.assertEqual(err.exception.status, 409)  # the first is still pending

    def test_llm_tiers_wait_for_capacity_and_presets_jump_ahead(self):
        self.dispatcher.capacity = 0
        novel = self.submit(self.a, text("a swarm of paper birds"))
        preset = self.submit(self.b, ing("asset", "fireball"))
        self.assertIs(self.engine.dispatch_next(), preset)
        self.assertIsNone(self.engine.dispatch_next())
        self.assertIs(novel.status, Status.QUEUED)
        self.dispatcher.capacity = 1
        self.assertIs(self.engine.dispatch_next(), novel)
        self.assertIs(novel.status, Status.FORGING)
        request, done = self.dispatcher.jobs[0]
        done(request, resolve_preset(Recipe.parse([ing("asset", "stone-block")], CATALOG), CATALOG), None)
        self.assertIs(novel.status, Status.READY)

    def test_remix_served_before_novel(self):
        novel = self.submit(self.a, text("a swarm of paper birds"))
        remix = self.submit(self.b, ing("asset", "fireball"), text("that splits"))
        self.assertIs(self.engine.dispatch_next(), remix)
        self.assertIs(novel.status, Status.QUEUED)

    def test_outcomes_and_scoring(self):
        self.engine.start_round()
        hit = self.submit(self.a, ing("asset", "fireball"))
        caught = self.submit(self.b, ing("spell", "thunder-lob"))
        self.engine.dispatch_next(); self.engine.dispatch_next()
        self.engine.report("hit", hit.id)
        self.engine.report("catch", caught.id)
        self.assertEqual(self.engine.cracks, 1)
        self.assertEqual(self.engine.scores[self.a.team], 10)
        self.assertEqual(self.engine.charge, 3)
        buzz = collect(self.hub, "audience", self.b.id)
        self.engine.report("return", caught.id)
        self.assertEqual(self.engine.scores[self.b.team], -5)
        self.assertEqual([d["spell"] for e, d in buzz() if e == "audience:buzz"], ["Thunder Lob"])
        with self.assertRaises(GameError):
            self.engine.report("hit", hit.id)  # no longer in flight

    def test_in_flight_limit_and_fizzle(self):
        self.engine.start_round()
        members = [self.engine.join(f"p{i}") for i in range(ACTS[0].max_in_flight + 1)]
        reqs = [self.engine.submit(m, [ing("asset", "fireball")]) for m in members]
        for _ in reqs:
            self.engine.dispatch_next()
        self.assertEqual([r.status for r in reqs].count(Status.CAST), ACTS[0].max_in_flight)
        self.clock.t += SPELL_LIFETIME_S + 1
        self.engine.tick()
        self.assertIs(reqs[0].status, Status.FIZZLED)
        self.assertIs(reqs[-1].status, Status.CAST)

    def test_round_ends(self):
        self.engine.start_round()
        queued = self.submit(self.a, text("a dragon"))
        self.clock.t = self.engine.round_started + ACTS[0].seconds + 1
        self.engine.tick()
        self.assertEqual(self.engine.phase, "storm")
        self.clock.t = self.engine.round_started + ROUND_SECONDS
        self.engine.tick()
        self.assertEqual((self.engine.phase, self.engine.outcome), ("over", "hatched"))
        self.assertIs(queued.status, Status.EXPIRED)
        self.assertEqual(len(self.engine.queue), 0)
        with self.assertRaises(GameError):
            self.engine.submit(self.b, [ing("asset", "fireball")])

    def test_egg_breaks(self):
        self.engine.start_round()
        for i in range(MAX_CRACKS):
            r = self.submit(self.engine.join(f"p{i}"), ing("asset", "fireball"))
            self.engine.dispatch_next()
            self.engine.report("hit", r.id)
        self.assertEqual((self.engine.phase, self.engine.outcome), ("over", "broken"))

    def test_host_view(self):
        self.dispatcher.capacity = 0
        novel = self.submit(self.a, text("a dragon"))
        preset = self.submit(self.b, ing("asset", "fireball"))
        host = collect(self.hub, "host")
        self.engine.tick()
        view = [d for e, d in host() if e == "host:view"][-1]
        self.assertEqual([r["id"] for r in view["queue"]], [preset.id, novel.id])
        self.assertEqual(view["queue"][1]["caster"]["name"], "Maya")
        self.assertEqual([m["name"] for m in view["roster"]], ["Maya", "Theo"])
        self.engine.tick()
        self.assertEqual([e for e, _ in host() if e == "host:view"], [])  # only published on changes
        self.engine.dispatch_next()
        self.engine.tick()
        view = [d for e, d in host() if e == "host:view"][-1]
        self.assertEqual(([r["id"] for r in view["queue"]], [r["id"] for r in view["ready"]]), ([novel.id], [preset.id]))

    def test_reset_expires_popped_request(self):
        r = self.submit(self.a, ing("asset", "fireball"))
        popped = self.engine.queue.pop(lambda _: True, 0)  # as if the dispatcher popped it, then reset ran
        self.engine.reset()
        self.assertIs(r.status, Status.EXPIRED)
        self.assertIs(popped, r)


if __name__ == "__main__":
    unittest.main()
