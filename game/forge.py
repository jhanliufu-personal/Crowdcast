"""Forges turn a claimed spell request into a spell. This is the interface the LLM harness implements.

The engine claims a request from the queue and hands it to a forge as a ForgeJob:

    PRESET          → CatalogForge (here): resolves from the catalog and acks immediately.
    REMIX and NOVEL → the LLM forge (to be built): calls the LLM, then acks or nacks later.

Both use the same contract, so the queue doesn't care which one ran. The request stays in the queue
(status "forging") until the forge answers its job exactly once:

    job.ack(spell)   served; spell is a dict, see spell_request.validate_spell for the contract
    job.nack(error)  could not make it; the request is retried once, then marked failed

Call either one from any thread. Each returns True if the engine accepted the answer and False if the job
is no longer current (the host reset the round, the lease ran out and the request was handed out
again, or the job was already answered); then throw the result away. An ack whose spell fails
validation counts as a nack and returns False.

An LLM forge is any object with:

    capacity: int                     how many jobs it runs at once; the engine never hands it more
    submit(job: ForgeJob) -> None     start the work and return quickly (use a thread or pool)

A job's lease lasts LEASE_SECONDS[tier] (in engine.py); after that it counts as a nack. Check
job.is_current() between expensive steps to stop early after a reset or timeout.
"""

from .spell_request import resolve_preset


class ForgeJob:
    def __init__(self, request, lease, catalog, on_ack, on_nack, is_current, seconds_left):
        self.request = request  # SpellRequest: .id, .tier, .recipe, .attempts, .member_id
        self.lease = lease      # request_queue.Lease: proof of this claim
        self.catalog = catalog  # catalog.Catalog: shapes, assets, motions, preset spells
        self._on_ack = on_ack
        self._on_nack = on_nack
        self._is_current = is_current
        self._seconds_left = seconds_left

    def brief(self):
        """What the request asks for, ready to put in a prompt."""
        return {
            "request_id": self.request.id,
            "tier": self.request.tier.name.lower(),
            "attempt": self.lease.attempt,
            "parts": self.request.recipe.expand(self.catalog),  # ingredients carry their catalog entry
            "text": self.request.recipe.text,
        }

    def ack(self, spell):
        return self._on_ack(self.lease, spell)

    def nack(self, error):
        return self._on_nack(self.lease, error)

    def is_current(self):
        return self._is_current(self.lease)

    def seconds_left(self):
        return self._seconds_left(self.lease)


class CatalogForge:
    """PRESET requests: everything already exists, so the answer is immediate."""

    capacity = None  # unlimited

    def submit(self, job):
        job.ack(resolve_preset(job.request.recipe, job.catalog))


class NotConnectedForge:
    """Stand-in LLM forge: zero capacity, so REMIX and NOVEL requests wait in the queue."""

    capacity = 0
    online = False

    def submit(self, job):
        raise NotImplementedError("no LLM forge is connected")
