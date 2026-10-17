"""python -m unittest discover tests"""

import json
import unittest

from game.catalog import Catalog
from game.engine import (ACTS, COOLDOWN_S, LEASE_SECONDS, MAX_CRACKS, ROUND_SECONDS, SPELL_LIFETIME_S, GameEngine,
                         GameError)
from game.events import EventHub
from game.request_queue import RequestQueue
from game.spell_request import Recipe, RequestError, SpellRequest, Status, Tier, resolve_preset, validate_spell

CATALOG = Catalog.load()


def catalog_with(**changes):
    """A copy of the catalog with some top-level settings replaced."""
    return Catalog({**CATALOG.data, **changes})


def ing(kind, block_id):
    return {"type": "ingredient", "kind": kind, "id": block_id}


def text(t):
    return {"type": "text", "text": t}


def preset_spell(asset="stone-block"):
    return resolve_preset(Recipe.parse([ing("asset", asset)], CATALOG), CATALOG)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeForge:
    """An LLM forge that keeps its jobs so the test can answer them."""

    online = True

    def __init__(self, capacity=1):
        self.capacity = capacity
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)


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


class SpellContractTest(unittest.TestCase):
    def test_presets_pass(self):
        for spell_id in CATALOG.spells:
            spell = resolve_preset(Recipe.parse([ing("spell", spell_id)], CATALOG), CATALOG)
            self.assertIsNone(validate_spell(spell, CATALOG)[1], spell_id)

    def test_minimal_llm_spell(self):
        spell, error = validate_spell({
            "name": "Paper Swarm", "incantation": "Papyrix",
            "asset": {"shape": "book", "color": "#f2ead3", "size": 9},
            "motion": {"id": "zigzag"},
            "code": "…",  # extra fields pass through
        }, CATALOG)
        self.assertIsNone(error)
        self.assertEqual((spell["asset"]["size"], spell["power"], spell["motion"]["speed"]), (1.5, 1, 2.0))
        self.assertEqual(spell["code"], "…")

    def test_rejects(self):
        good = {"name": "X", "incantation": "Xyzzy", "asset": {"shape": "box", "color": "#ffffff"}, "motion": {"id": "arc"}}
        for broken in ({**good, "incantation": "two words"}, {**good, "asset": {"shape": "teapot", "color": "#ffffff"}},
                       {**good, "asset": {"shape": "box", "color": "red"}}, {**good, "motion": {"id": "teleport"}},
                       {**good, "name": ""}, "not a dict"):
            with self.subTest(broken=broken):
                self.assertIsNotNone(validate_spell(broken, CATALOG)[1])


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.q = RequestQueue(LEASE_SECONDS, max_attempts=2, clock=self.clock)

    def push(self, *parts):
        recipe = Recipe.parse(list(parts), CATALOG)
        request = SpellRequest("m", recipe, recipe.tier())
        self.q.push(request)
        return request

    def test_order_and_gating(self):
        novel = self.push(text("a dragon"))
        remix = self.push(ing("asset", "fireball"), text("bigger"))
        p1 = self.push(ing("asset", "fireball"))
        p2 = self.push(ing("spell", "ember-bolt"))
        self.assertEqual(self.q.ahead_of(novel), 3)
        presets_only = lambda r: r.tier is Tier.PRESET
        self.assertIs(self.q.claim(presets_only, 0)[0], p1)
        self.assertIs(self.q.claim(presets_only, 0)[0], p2)
        self.assertIsNone(self.q.claim(presets_only, 0))
        self.assertIs(self.q.claim(lambda r: True, 0)[0], remix)
        self.assertIs(self.q.claim(lambda r: True, 0)[0], novel)

    def test_claimed_stays_until_ack(self):
        r = self.push(ing("asset", "fireball"))
        _, lease = self.q.claim(lambda _: True, 0)
        self.assertEqual(len(self.q), 1)
        self.assertIsNone(self.q.claim(lambda _: True, 0))  # claimed: not handed out twice
        self.assertIs(self.q.ack(lease), r)
        self.assertEqual(len(self.q), 0)
        self.assertIsNone(self.q.ack(lease))  # answered once only

    def test_nack_retries_in_place_then_gives_up(self):
        first = self.push(text("a dragon"))
        second = self.push(text("a kraken"))
        _, lease = self.q.claim(lambda _: True, 0)
        self.assertEqual(self.q.nack(lease), (first, True))
        request, lease2 = self.q.claim(lambda _: True, 0)
        self.assertIs(request, first)  # kept its place ahead of `second`
        self.assertEqual(lease2.attempt, 2)
        self.assertIsNone(self.q.ack(lease))  # the first attempt's lease is stale
        self.assertEqual(self.q.nack(lease2), (first, False))
        self.assertEqual([r for r, _ in self.q.snapshot()], [second])

    def test_lease_timeout(self):
        r = self.push(text("a dragon"))
        _, lease = self.q.claim(lambda _: True, 0)
        self.clock.t += LEASE_SECONDS[Tier.NOVEL] - 1
        self.assertEqual(self.q.expire_leases(), [])
        self.clock.t += 1
        self.assertEqual(self.q.expire_leases(), [(r, True)])
        self.assertFalse(self.q.is_current(lease))

    def test_clear_invalidates_leases(self):
        self.push(ing("asset", "fireball"))
        _, lease = self.q.claim(lambda _: True, 0)
        self.q.clear()
        self.assertIsNone(self.q.ack(lease))
        self.assertIsNone(self.q.nack(lease))


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.hub = EventHub()
        self.forge = FakeForge()
        # Every spell at the egg, so tests don't depend on a random target; TargetingTest covers the others.
        self.engine = GameEngine(catalog_with(targeting={"mode": "egg"}), self.hub, self.forge, clock=self.clock)
        self.a = self.engine.join("Maya")
        self.b = self.engine.join("Theo")

    def submit(self, member, *parts):
        request = self.engine.submit(member, list(parts))
        self.clock.t += COOLDOWN_S
        return request

    def cast(self, member, *parts):
        """Submit a preset during a round and confirm it spawned; returns the request."""
        request = self.submit(member, *parts)
        self.engine.dispatch_next()
        self.engine.report("spawned", request.id)
        return request

    def test_teams_balanced(self):
        self.assertNotEqual(self.a.team, self.b.team)

    def test_preset_is_acked_immediately_and_waits_for_round(self):
        headset = collect(self.hub)
        r = self.submit(self.a, ing("spell", "ember-bolt"))
        self.engine.dispatch_next()
        self.assertIs(r.status, Status.READY)
        self.assertEqual(len(self.engine.queue), 0)
        self.assertEqual([e for e, _ in headset() if e == "spell:cast"], [])
        self.engine.start_round()
        self.assertIs(r.status, Status.SENT)
        casts = [d for e, d in headset() if e == "spell:cast"]
        self.assertEqual((casts[0]["id"], casts[0]["caster"]["name"]), (r.id, "Maya"))

    def test_cooldown_and_one_pending(self):
        self.engine.submit(self.a, [ing("asset", "fireball")])
        with self.assertRaises(GameError) as err:
            self.engine.submit(self.a, [ing("asset", "fireball")])
        self.assertEqual(err.exception.status, 429)
        self.clock.t += COOLDOWN_S
        with self.assertRaises(GameError) as err:
            self.engine.submit(self.a, [ing("asset", "fireball")])
        self.assertEqual(err.exception.status, 409)  # the first is still pending

    def test_llm_job_stays_queued_until_ack(self):
        novel = self.submit(self.a, text("a swarm of paper birds"))
        self.assertIs(self.engine.dispatch_next(), novel)
        self.assertIs(novel.status, Status.FORGING)
        self.assertEqual(len(self.engine.queue), 1)  # claimed, not removed
        job = self.forge.jobs[0]
        self.assertEqual(job.brief()["text"], "a swarm of paper birds")
        self.assertTrue(job.ack(preset_spell()))
        self.assertIs(novel.status, Status.READY)
        self.assertEqual(len(self.engine.queue), 0)

    def test_capacity_and_presets_overtake(self):
        self.forge.capacity = 0
        novel = self.submit(self.a, text("a swarm of paper birds"))
        preset = self.submit(self.b, ing("asset", "fireball"))
        self.assertIs(self.engine.dispatch_next(), preset)
        self.assertIsNone(self.engine.dispatch_next())
        self.assertIs(novel.status, Status.QUEUED)
        self.forge.capacity = 1
        self.assertIs(self.engine.dispatch_next(), novel)
        self.assertIsNone(self.engine.dispatch_next())  # capacity 1 is in use until it answers

    def test_remix_served_before_novel(self):
        novel = self.submit(self.a, text("a swarm of paper birds"))
        remix = self.submit(self.b, ing("asset", "fireball"), text("that splits"))
        self.assertIs(self.engine.dispatch_next(), remix)
        self.assertIs(novel.status, Status.QUEUED)

    def test_nack_retries_then_fails(self):
        r = self.submit(self.a, text("a dragon"))
        self.engine.dispatch_next()
        self.assertTrue(self.forge.jobs[0].nack("LLM timeout"))
        self.assertIs(r.status, Status.QUEUED)
        self.engine.dispatch_next()
        self.assertEqual(self.forge.jobs[1].lease.attempt, 2)
        self.assertTrue(self.forge.jobs[1].nack("LLM timeout"))
        self.assertIs(r.status, Status.FAILED)
        self.assertEqual(r.error, "LLM timeout")

    def test_invalid_spell_counts_as_nack(self):
        r = self.submit(self.a, text("a dragon"))
        self.engine.dispatch_next()
        with self.assertLogs("game.engine", "WARNING"):
            self.assertFalse(self.forge.jobs[0].ack({"name": "Dragon"}))
        self.assertIs(r.status, Status.QUEUED)  # retrying

    def test_lease_timeout_then_late_ack_rejected(self):
        r = self.submit(self.a, text("a dragon"))
        self.engine.dispatch_next()
        late = self.forge.jobs[0]
        self.clock.t += LEASE_SECONDS[Tier.NOVEL]
        self.engine.tick()
        self.assertIs(r.status, Status.QUEUED)
        self.assertFalse(late.is_current())
        self.assertFalse(late.ack(preset_spell()))
        self.engine.dispatch_next()
        self.assertTrue(self.forge.jobs[1].ack(preset_spell()))
        self.assertIs(r.status, Status.READY)

    def test_reset_empties_queue_and_rejects_late_acks(self):
        claimed = self.submit(self.a, text("a dragon"))
        waiting = self.submit(self.b, text("a kraken"))
        self.engine.dispatch_next()
        self.engine.reset()
        self.assertEqual(len(self.engine.queue), 0)
        self.assertEqual((claimed.status, waiting.status), (Status.EXPIRED, Status.EXPIRED))
        self.assertFalse(self.forge.jobs[0].ack(preset_spell()))
        self.assertIs(claimed.status, Status.EXPIRED)

    def test_delivery_ack_and_resend(self):
        self.engine.start_round()
        r = self.submit(self.a, ing("asset", "fireball"))
        self.engine.dispatch_next()
        self.assertIs(r.status, Status.SENT)
        self.assertEqual([s["id"] for s in self.engine.unconfirmed_casts()], [r.id])
        self.clock.t = self.engine.round_started + SPELL_LIFETIME_S * 2
        self.engine.tick()
        self.assertIs(r.status, Status.SENT)  # no fizzle timer until the headset confirms
        self.engine.report("spawned", r.id)
        self.assertIs(r.status, Status.CAST)
        self.assertEqual(self.engine.unconfirmed_casts(), [])
        self.clock.t += SPELL_LIFETIME_S + 1
        self.engine.tick()
        self.assertIs(r.status, Status.FIZZLED)

    def test_outcomes_and_scoring(self):
        self.engine.start_round()
        hit = self.cast(self.a, ing("asset", "fireball"))
        caught = self.cast(self.b, ing("spell", "thunder-lob"))
        self.engine.report("hit", hit.id)
        self.engine.report("catch", caught.id)
        self.assertEqual(self.engine.cracks, 1)
        self.assertEqual(self.engine.scores[self.a.team], 10)
        self.assertEqual(self.engine.charge, 3)
        buzz = collect(self.hub, "audience", self.b.id)
        view = self.engine.host_view()
        self.assertEqual([(r["id"], r["status"]) for r in view["recent"]], [(hit.id, "hit"), (caught.id, "caught")])
        self.engine.report("return", caught.id)
        self.assertEqual(self.engine.scores[self.b.team], -5)
        self.assertEqual([d["spell"] for e, d in buzz() if e == "audience:buzz"], ["Thunder Lob"])
        with self.assertRaises(GameError):
            self.engine.report("hit", hit.id)  # no longer in flight

    def test_in_flight_limit(self):
        self.engine.start_round()
        members = [self.engine.join(f"p{i}") for i in range(ACTS[0].max_in_flight + 1)]
        reqs = [self.engine.submit(m, [ing("asset", "fireball")]) for m in members]
        for _ in reqs:
            self.engine.dispatch_next()
        self.assertEqual([r.status for r in reqs].count(Status.SENT), ACTS[0].max_in_flight)
        self.assertIs(reqs[-1].status, Status.READY)
        self.engine.report("shield", reqs[0].id)
        self.assertIs(reqs[-1].status, Status.SENT)

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
            r = self.cast(self.engine.join(f"p{i}"), ing("asset", "fireball"))
            self.engine.report("hit", r.id)
        self.assertEqual((self.engine.phase, self.engine.outcome), ("over", "broken"))

    def test_host_view(self):
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
        self.engine.dispatch_next()
        self.engine.tick()
        view = [d for e, d in host() if e == "host:view"][-1]
        self.assertEqual([r["id"] for r in view["ready"]], [preset.id])
        self.assertEqual([(r["id"], r["status"], r["lease"]["attempt"]) for r in view["queue"]], [(novel.id, "forging", 1)])
        self.assertEqual(view["queue"][0]["lease"]["seconds"], LEASE_SECONDS[Tier.NOVEL])
        self.assertEqual(view["ready"][0]["spell"]["asset"]["emoji"], "🔥")  # full spell, for the player view


if __name__ == "__main__":
    unittest.main()


class TargetingTest(unittest.TestCase):
    setUp, submit, cast = EngineTest.setUp, EngineTest.submit, EngineTest.cast

    def use(self, mode):
        self.engine.catalog = catalog_with(targeting={"mode": mode})

    def test_user_hit_costs_hp_then_heals(self):
        self.use("user")
        player = CATALOG.player
        self.engine.start_round()
        r = self.cast(self.a, ing("spell", "ember-bolt"))
        self.assertEqual(r.spell["target"], "user")
        self.engine.report("hit", r.id)
        self.assertEqual(self.engine.cracks, 0)
        hp = player["max_hp"] - player["damage_per_power"] * r.spell["power"]
        self.assertEqual(self.engine.state()["player"]["hp"], hp)
        self.engine.tick()
        self.clock.t += 3
        self.engine.tick()
        self.assertAlmostEqual(self.engine.player_hp, hp + 3 * player["heal_per_s"])
        self.clock.t += 1000
        self.engine.tick()
        self.assertEqual(self.engine.player_hp, player["max_hp"])  # never above the maximum

    def test_harness_mode_uses_the_forge_choice(self):
        self.use("harness")
        novel = self.submit(self.a, text("a sneaky dart"))
        self.engine.dispatch_next()
        self.assertTrue(self.forge.jobs[0].ack({**preset_spell(), "target": "user"}))
        self.assertEqual(novel.spell["target"], "user")

    def test_harness_mode_picks_for_presets(self):
        self.use("harness")
        r = self.submit(self.a, ing("spell", "ember-bolt"))
        self.engine.dispatch_next()
        self.assertIn(r.spell["target"], ("egg", "user"))

    def test_fixed_mode_overrides_the_forge(self):
        self.use("egg")
        novel = self.submit(self.a, text("a sneaky dart"))
        self.engine.dispatch_next()
        self.forge.jobs[0].ack({**preset_spell(), "target": "user"})
        self.assertEqual(novel.spell["target"], "egg")

    def test_invalid_target_is_dropped(self):
        spell, error = validate_spell({**preset_spell(), "target": "moon"}, CATALOG)
        self.assertIsNone(error)
        self.assertNotIn("target", spell)
