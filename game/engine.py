"""Game logic: the round, the egg, scores, and the pipeline every audience spell goes through.

    submit ─▶ RequestQueue ──claim──┬─ PRESET ─────── CatalogForge ─────┐
             (tier, arrival)        └─ REMIX/NOVEL ── LLM forge ────────┤ job.ack(spell): removed from
                   ▲                                                    │ the queue, validated, ready
                   └──────── job.nack() or lease timeout: retry ◀───────┤
                                                                        ▼
    headset ──"spawned" ack──▶ cast ◀── sent ◀── paced by the act ◀── ready

Two acknowledgements, one per hand-off: a forge acks "the spell is built" (forge.py), and the
headset acks "the spell is in the scene" (report("spawned")). Until then the request stays where
it was: in the queue, or sent again to a headset that reconnects.

The server owns the session (members, scores, queue). Everyone in the audience plays for themselves. The headset owns the simulation and
reports what happened to each spell (hit / catch / shield / return); the engine scores it.
"""

import colorsys
import logging
import math
import random
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .catalog import TARGETS
from .forge import CatalogForge, ForgeJob, NotConnectedForge
from .request_queue import RequestQueue
from .spell_request import PENDING, Recipe, SpellRequest, Status, Tier, validate_spell

log = logging.getLogger(__name__)



def member_color(index):
    """A distinct color per audience member: hues a golden angle apart, so neighbours in join order differ."""
    r, g, b = colorsys.hls_to_rgb((index * 0.381966) % 1.0, 0.55, 0.75)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"



@dataclass(frozen=True)
class Act:
    name: str
    seconds: float
    max_in_flight: int  # spells in the scene at once


ACTS = (Act("dusk", 60, 2), Act("storm", 90, 5), Act("midnight", 30, 3))
ACT_NAMES = {a.name for a in ACTS}
ROUND_SECONDS = sum(a.seconds for a in ACTS)
MAX_CRACKS = 5
COOLDOWN_S = 5
SPELL_LIFETIME_S = 20  # a spell in the scene with no reported outcome fizzles after this
LEASE_SECONDS = {Tier.PRESET: 5, Tier.REMIX: 20, Tier.NOVEL: 60}  # a forge claim lapses after this
MAX_ATTEMPTS = 2  # forge claims per request before it fails
HIT_POINTS = 10
RETURN_PENALTY = 5
LEADERBOARD = 5  # top scorers in the game state
STATE_EVERY_S = 1.0
HISTORY = 10  # requests shown per member


class GameError(Exception):
    """A request the game refuses. The message is shown to the client."""

    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


@dataclass
class Member:
    id: str
    token: str
    name: str
    color: str
    score: int = 0
    last_cast_at: float = None
    request_ids: list = field(default_factory=list)
    joined_at: float = field(default_factory=time.time)

    def to_public(self):
        return {"id": self.id, "name": self.name, "color": self.color}


def act_at(elapsed):
    for act in ACTS:
        if elapsed < act.seconds:
            return act
        elapsed -= act.seconds
    return ACTS[-1]


class GameEngine:
    def __init__(self, catalog, hub, llm_forge=None, clock=time.monotonic):
        self.catalog = catalog
        self.hub = hub
        self.catalog_forge = CatalogForge()
        self.llm_forge = llm_forge or NotConnectedForge()
        self.clock = clock
        self.queue = RequestQueue(LEASE_SECONDS, MAX_ATTEMPTS, clock)
        self.members = {}
        self.requests = {}
        self._by_token = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._host_dirty = True  # the host view changed since it was last published
        self._new_round()

    def _new_round(self):
        self.phase = "lobby"  # lobby → dusk → storm → midnight → over
        self.outcome = None   # "hatched" | "broken"
        self.round_started = None
        self.cracks = 0
        self.charge = 0
        self.player_hp = float(self.catalog.player["max_hp"])  # the player in the headset
        self._healed_at = None
        for member in self.members.values():
            member.score = 0
        self.ready = deque()  # resolved spells waiting for room in the sky
        self.in_flight = {}   # id -> request, SENT to the headset or CAST into the scene
        self.orbs = []        # caught spells the player can return
        self.recent = deque(maxlen=20)  # requests that just left the sky, for the host's player view
        self._last_state_at = -math.inf

    # ---- threads -------------------------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._dispatch_loop, name="game-dispatch", daemon=True).start()
        threading.Thread(target=self._tick_loop, name="game-tick", daemon=True).start()

    def stop(self):
        self._stop.set()
        self.queue.wake()

    @property
    def stopped(self):
        return self._stop.is_set()

    def _dispatch_loop(self):
        while not self._stop.is_set():
            self.dispatch_next(timeout=0.5)

    def _tick_loop(self):
        while not self._stop.wait(0.25):
            self.tick()

    # ---- audience ------------------------------------------------------------------------------

    def join(self, name):
        name = " ".join(str(name or "").split())[:20]
        if not name:
            raise GameError("Pick a name first.")
        with self._lock:
            member = Member(id=f"a{secrets.token_hex(3)}", token=secrets.token_urlsafe(16), name=name,
                            color=member_color(len(self.members)))
            self.members[member.id] = member
            self._by_token[member.token] = member
            self._publish_state(force=True)
        return member

    def member_by_token(self, token):
        with self._lock:
            return self._by_token.get(token)

    def submit(self, member, raw_parts):
        recipe = Recipe.parse(raw_parts, self.catalog)
        with self._lock:
            if self.phase == "over":
                raise GameError("The round is over. Wait for the next one.", 409)
            now = self.clock()
            wait = self._cooldown(member, now)
            if wait > 0:
                raise GameError(f"Your wand is recharging ({math.ceil(wait)} s).", 429, retry_after=math.ceil(wait))
            if any(self.requests[r].status in PENDING for r in member.request_ids):
                raise GameError("Your last spell is still on its way.", 409)
            request = SpellRequest(member.id, recipe, recipe.tier())
            self.requests[request.id] = request
            member.request_ids.append(request.id)
            member.last_cast_at = now
            self.queue.push(request)
            self._publish_queue_positions()
            self._publish_state(force=True)
        return request

    def member_view(self, member):
        with self._lock:
            return {
                "member": member.to_public(),
                "requests": [self._request_public(self.requests[r]) for r in member.request_ids[-HISTORY:]],
                "cooldown_s": round(max(0.0, self._cooldown(member, self.clock())), 1),
            }

    def _cooldown(self, member, now):
        return 0.0 if member.last_cast_at is None else COOLDOWN_S - (now - member.last_cast_at)

    # ---- host ----------------------------------------------------------------------------------

    def start_round(self):
        with self._lock:
            if self.phase in ACT_NAMES:
                raise GameError("A round is already running.", 409)
            if self.phase == "over":
                self._new_round()
            self.phase = ACTS[0].name
            self.round_started = self.clock()
            self._release_ready()
            self._publish_state(force=True)

    def reset(self):
        """Back to the lobby: every pending spell expires, every spell in flight fizzles. Members stay."""
        with self._lock:
            self._clear_spells()
            self._new_round()
            self._publish_state(force=True)

    def _clear_spells(self):
        # Clearing the queue also invalidates every forge lease, so late acks are rejected.
        for request in self.queue.clear() + list(self.ready):
            self._set_status(request, Status.EXPIRED)
        for request in self.in_flight.values():
            self._set_status(request, Status.FIZZLED)
        self.ready.clear()
        self.in_flight.clear()

    # ---- headset -------------------------------------------------------------------------------

    def report(self, event_type, spell_id, target_member_id=None):
        """Apply an event from the headset: spawned (delivery ack), hit, catch, shield, or return."""
        with self._lock:
            if self.phase not in ACT_NAMES:
                raise GameError("No round is running.", 409)
            if event_type == "return":
                self._return_spell(spell_id, target_member_id)
            elif event_type == "spawned":
                request = self.in_flight.get(spell_id)
                if request is None:
                    raise GameError(f"Spell {spell_id} wasn't sent to the headset.", 404)
                if request.status is Status.SENT:  # repeats are harmless
                    request.cast_at = self.clock()  # the fizzle timer starts now
                    self._set_status(request, Status.CAST)
            else:
                status = {"hit": Status.HIT, "catch": Status.CAUGHT, "shield": Status.SHIELDED}.get(event_type)
                if status is None:
                    raise GameError(f"Unknown event type {event_type!r}.")
                request = self.in_flight.pop(spell_id, None)  # an outcome also implies it spawned
                if request is None:
                    raise GameError(f"Spell {spell_id} isn't in flight.", 404)
                self._set_status(request, status)
                if status is Status.HIT:
                    self.members[request.member_id].score += HIT_POINTS
                    if request.spell.get("target") == "user":
                        damage = self.catalog.player["damage_per_power"] * max(1, request.spell.get("power", 1))
                        self.player_hp = max(0.0, self.player_hp - damage)
                    else:
                        self.cracks += 1
                        if self.cracks >= MAX_CRACKS:
                            self._end_round("broken")
                elif status is Status.CAUGHT:
                    self.charge += request.spell["power"]
                    self.orbs.append(request)
            self._release_ready()
            self._publish_state(force=True)
            return self.state()

    def _return_spell(self, spell_id, target_member_id):
        orb = next((r for r in self.orbs if r.id == spell_id), None)
        if orb is None:
            raise GameError(f"Spell {spell_id} isn't a caught orb.", 404)
        target = self.members.get(target_member_id or orb.member_id)
        if target is None:
            raise GameError(f"No audience member {target_member_id}.", 404)
        self.orbs.remove(orb)
        target.score -= RETURN_PENALTY
        self.hub.publish(
            "audience:buzz",
            {"spell": orb.spell["name"], "incantation": orb.spell["incantation"], "penalty": RETURN_PENALTY},
            member_id=target.id,
        )

    # ---- pipeline ------------------------------------------------------------------------------

    def dispatch_next(self, timeout=0):
        """Claim the next request a forge can take and hand it over. It stays queued until answered."""
        claimed = self.queue.claim(self._can_take, timeout)
        if claimed is None:
            return None
        request, lease = claimed
        job = ForgeJob(request, lease, self.catalog, self._ack, self._nack, self.queue.is_current, self._seconds_left)
        if request.tier is Tier.PRESET:
            self.catalog_forge.submit(job)  # acks before returning
            return request
        with self._lock:
            request.attempts = lease.attempt
            self._set_status(request, Status.FORGING)
        try:
            self.llm_forge.submit(job)
        except Exception as err:
            log.exception("LLM forge failed to start %s", request.id)
            job.nack(f"forge error: {err}")
        return request

    def _can_take(self, request):
        # Runs under the queue's lock: only queue and forge state here, never the engine's lock.
        if request.tier is Tier.PRESET:
            return True
        return self.queue.claimed_count({Tier.REMIX, Tier.NOVEL}) < self.llm_forge.capacity

    def _seconds_left(self, lease):
        return max(0.0, lease.deadline - self.clock())

    def _ack(self, lease, spell):
        """ForgeJob.ack: the forge built the spell. Returns False if the job is no longer current."""
        spell, error = validate_spell(spell, self.catalog)
        if error:
            log.warning("forge ack for %s rejected, counting it as a nack: %s", lease.request_id, error)
            self._nack(lease, f"invalid spell: {error}")
            return False
        with self._lock:
            request = self.queue.ack(lease)
            if request is None:
                return False
            request.spell = spell
            request.error = None
            self._make_ready(request)
            self._publish_queue_positions()
        self.queue.wake()  # forge capacity freed up
        return True

    def _nack(self, lease, error):
        """ForgeJob.nack: the forge couldn't build it. Returns False if the job is no longer current."""
        with self._lock:
            result = self.queue.nack(lease)
            if result is None:
                return False
            self._after_release(*result, error)
        self.queue.wake()
        return True

    def _after_release(self, request, retrying, error):
        if retrying:
            self._set_status(request, Status.QUEUED)
            self._publish_queue_positions()
        else:
            request.error = error or "The forge couldn't make that spell."
            self._set_status(request, Status.FAILED)

    def _make_ready(self, request):
        member = self.members[request.member_id]
        request.spell = {**request.spell, "id": request.id, "caster": member.to_public(), "target": self._target(request.spell)}
        self._set_status(request, Status.READY)
        self.ready.append(request)
        self._release_ready()

    def _target(self, spell):
        """What a spell flies at, by the catalog's targeting mode (see Catalog.targeting)."""
        mode = self.catalog.targeting["mode"]
        if mode in TARGETS:
            return mode
        if mode == "harness" and spell.get("target") in TARGETS:
            return spell["target"]  # the forge chose
        return "egg" if random.random() < self.catalog.targeting["egg_share"] else "user"

    def _heal(self, now):
        """The player slowly gets health back during a round."""
        if self._healed_at is not None:
            hp_max = self.catalog.player["max_hp"]
            self.player_hp = min(hp_max, self.player_hp + self.catalog.player["heal_per_s"] * (now - self._healed_at))
        self._healed_at = now if self.phase in ACT_NAMES else None

    def _release_ready(self):
        """Cast ready spells while the current act has room for more in the sky."""
        if self.phase not in ACT_NAMES:
            return
        limit = act_at(self.clock() - self.round_started).max_in_flight
        while self.ready and len(self.in_flight) < limit:
            request = self.ready.popleft()
            self.in_flight[request.id] = request
            self._set_status(request, Status.SENT)  # CAST once the headset reports "spawned"
            self.hub.publish("spell:cast", request.spell, roles=("headset", "spectator"))

    def unconfirmed_casts(self):
        """Spells sent to the headset that it hasn't confirmed; sent again when a headset connects."""
        with self._lock:
            return [r.spell for r in self.in_flight.values() if r.status is Status.SENT]

    def tick(self):
        with self._lock:
            for request, retrying in self.queue.expire_leases():
                self._after_release(request, retrying, "The forge took too long.")
            now = self.clock()
            self._heal(now)
            if self.phase in ACT_NAMES:
                elapsed = now - self.round_started
                if elapsed >= ROUND_SECONDS:
                    self._end_round("hatched")
                else:
                    act = act_at(elapsed).name
                    if act != self.phase:
                        self.phase = act
                        self._publish_state(force=True)
                    for request in [r for r in self.in_flight.values() if r.cast_at and now - r.cast_at > SPELL_LIFETIME_S]:
                        del self.in_flight[request.id]
                        self._set_status(request, Status.FIZZLED)
                    self._release_ready()
            self._publish_state()
            if self._host_dirty:
                self._host_dirty = False
                self.hub.publish("host:view", self.host_view(), roles=("host",))

    def _end_round(self, outcome):
        self.phase = "over"
        self.outcome = outcome
        caught = [{"name": r.spell["name"], "caster": r.spell["caster"]["name"]} for r in self.orbs]
        self._clear_spells()
        scores = self._scores()
        best = max(scores.values(), default=0)
        self.hub.publish(
            "game:over",
            {
                "outcome": outcome,
                "charge": self.charge,
                "caught": caught,
                "scores": scores,
                "winners": [m for m, s in scores.items() if s == best and best > 0],
                # TODO: the hatched creature is designed by the LLM from the caught spells.
            },
        )
        self._publish_state(force=True)

    # ---- state and publishing ------------------------------------------------------------------

    def state(self):
        with self._lock:
            elapsed = 0 if self.round_started is None else min(self.clock() - self.round_started, ROUND_SECONDS)
            return {
                "phase": self.phase,
                "outcome": self.outcome,
                "round_seconds": ROUND_SECONDS,
                "remaining_s": round(ROUND_SECONDS - elapsed, 1),
                "cracks": self.cracks,
                "max_cracks": MAX_CRACKS,
                "player": {"hp": round(self.player_hp, 1), "max_hp": self.catalog.player["max_hp"]},
                "targeting": self.catalog.targeting["mode"],
                "charge": self.charge,
                "scores": self._scores(),  # member id -> score
                "leaderboard": [{**m.to_public(), "score": m.score} for m in self._leaderboard()[:LEADERBOARD]],
                "queue": self.queue.counts(),
                "forging": self.queue.claimed_count(set(Tier)),
                "ready": len(self.ready),
                "in_flight": [r.spell for r in self.in_flight.values()],
                "orbs": [{"id": r.id, "name": r.spell["name"], "incantation": r.spell["incantation"]} for r in self.orbs],
                "audience": len(self.members),
                "forge_online": getattr(self.llm_forge, "online", True),
                "cooldown_s": COOLDOWN_S,
            }

    def _scores(self):
        return {m.id: m.score for m in self.members.values()}

    def _leaderboard(self):
        return sorted(self.members.values(), key=lambda m: (-m.score, m.joined_at))

    def mark_host_dirty(self):
        """Something in the host view changed; the tick loop publishes it within 0.25 s."""
        self._host_dirty = True

    def host_view(self):
        """Roster and pipeline for the host page, in the order things will happen."""
        with self._lock:
            online = self.hub.online_members()

            def row(request, ahead=None, full_spell=False):
                out = {**request.to_public(ahead=ahead), "caster": self.members[request.member_id].to_public()}
                if full_spell:
                    out["spell"] = request.spell  # asset, motion, timing: what the player view animates
                return out

            roster = []
            for m in sorted(self.members.values(), key=lambda m: m.joined_at):
                mine = [self.requests[r] for r in m.request_ids]
                roster.append({
                    **m.to_public(),
                    "online": m.id in online,
                    "joined_at": m.joined_at,
                    "score": m.score,
                    "casts": len(mine),
                    "hits": sum(r.status is Status.HIT for r in mine),
                    "latest": mine[-1].to_public() if mine else None,
                })
            queue = []
            for ahead, (request, lease) in enumerate(self.queue.snapshot()):
                entry = row(request, ahead)
                if lease:
                    entry["lease"] = {"attempt": lease.attempt, "seconds_left": round(self._seconds_left(lease), 1),
                                      "seconds": LEASE_SECONDS[request.tier]}
                queue.append(entry)
            return {
                "roster": roster,
                "queue": queue,  # claimed requests (status "forging") keep their place until acked
                "ready": [row(r, full_spell=True) for r in self.ready],
                "in_flight": [row(r, full_spell=True) for r in self.in_flight.values()],
                "recent": [{"id": r.id, "status": r.status.value} for r in self.recent],
            }

    def _publish_state(self, force=False):
        self._host_dirty |= force  # forced publishes are real changes; the periodic one is only the clock
        now = self.clock()
        if force or now - self._last_state_at >= STATE_EVERY_S:
            self._last_state_at = now
            self.hub.publish("game:state", self.state())

    def _set_status(self, request, status):
        in_sky = (Status.SENT, Status.CAST)
        if request.status in in_sky and status not in in_sky:
            self.recent.append(request)  # it just left the sky: hit, caught, shielded or fizzled
        request.status = status
        self._publish_request(request)

    def _request_public(self, request):
        queued = request.status in (Status.QUEUED, Status.FORGING)
        return request.to_public(ahead=self.queue.ahead_of(request) if queued else None)

    def _publish_request(self, request):
        self._host_dirty = True
        self.hub.publish("spell:status", self._request_public(request), member_id=request.member_id)

    def _publish_queue_positions(self):
        self._host_dirty = True
        for ahead, (request, _) in enumerate(self.queue.snapshot()):
            self.hub.publish("spell:status", request.to_public(ahead=ahead), member_id=request.member_id)
