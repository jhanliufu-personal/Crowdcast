"""Structured spell requests from the audience, and how much work each one is.

The web app's composer holds catalog blocks (chips) and free text, in the order the audience member
wrote them. That ordered list is the request, for example:

    [{"type": "ingredient", "kind": "asset", "id": "fireball"},
     {"type": "text", "text": "that splits into three"}]

How much work it takes decides its tier, which is also its queue priority:

    PRESET  only catalog blocks: at most one spell, one asset and one motion, and no text.
            Resolved straight from the catalog; never touches the LLM.
    REMIX   at least one existing spell or asset, plus text (a new motion, a small change) or
            several blocks to combine. The LLM makes a small, local edit.
    NOVEL   text with no spell or asset to start from (a motion block is allowed as a hint).
            The LLM builds the whole thing.
"""

import itertools
import re
import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum

from .catalog import KINDS

MAX_PARTS = 8
MAX_TEXT = 120  # characters of free text across the whole request


class Tier(IntEnum):
    """Queue priority: lower is served first."""

    PRESET = 0
    REMIX = 1
    NOVEL = 2


class Status(str, Enum):
    QUEUED = "queued"      # waiting in the request queue
    FORGING = "forging"    # claimed by the LLM forge, still in the queue until it acks
    READY = "ready"        # fully defined, waiting for room in the sky
    SENT = "sent"          # sent to the headset, waiting for it to confirm the spell is in the scene
    CAST = "cast"          # in the scene (the headset confirmed it)
    HIT = "hit"            # cracked the egg
    CAUGHT = "caught"      # the player caught it
    SHIELDED = "shielded"  # the player blocked it
    FIZZLED = "fizzled"    # no outcome before its lifetime ran out, or the round ended mid-flight
    FAILED = "failed"      # the LLM couldn't make it
    EXPIRED = "expired"    # the round ended (or was reset) before it was cast


PENDING = frozenset({Status.QUEUED, Status.FORGING, Status.READY})


class RequestError(ValueError):
    """A malformed request. The message is shown to the audience member."""


@dataclass(frozen=True)
class Ingredient:
    kind: str  # one of KINDS
    id: str    # catalog id

    def to_dict(self):
        return {"type": "ingredient", "kind": self.kind, "id": self.id}


@dataclass(frozen=True)
class Text:
    text: str

    def to_dict(self):
        return {"type": "text", "text": self.text}


@dataclass(frozen=True)
class Recipe:
    """What the audience member composed: catalog blocks and free text, in order."""

    parts: tuple

    @classmethod
    def parse(cls, raw, catalog):
        if not isinstance(raw, list):
            raise RequestError("A spell is a list of parts.")
        if len(raw) > MAX_PARTS:
            raise RequestError(f"Use at most {MAX_PARTS} pieces in one spell.")
        parts = []
        for item in raw:
            if not isinstance(item, dict):
                raise RequestError("Each part must be an object.")
            if item.get("type") == "ingredient":
                kind, block_id = item.get("kind"), item.get("id")
                if not (isinstance(kind, str) and isinstance(block_id, str) and catalog.has(kind, block_id)):
                    raise RequestError(f"Unknown building block: {kind}/{block_id}.")
                parts.append(Ingredient(kind, block_id))
            elif item.get("type") == "text":
                if not isinstance(item.get("text"), str):
                    raise RequestError("Text parts need a text field.")
                text = " ".join(item["text"].split())
                if text:
                    parts.append(Text(text))
            else:
                raise RequestError(f"Unknown part type: {item.get('type')!r}.")
        recipe = cls(tuple(parts))
        if len(recipe.text) > MAX_TEXT:
            raise RequestError(f"Keep the description under {MAX_TEXT} characters.")
        recipe.tier()  # raises if there's nothing to cast
        return recipe

    def ingredients(self, kind=None):
        return [p for p in self.parts if isinstance(p, Ingredient) and (kind is None or p.kind == kind)]

    @property
    def text(self):
        return " ".join(p.text for p in self.parts if isinstance(p, Text))

    def tier(self):
        counts = {k: len(self.ingredients(k)) for k in KINDS}
        things = counts["spell"] + counts["asset"]
        if not self.parts:
            raise RequestError("Tap a building block or describe a spell.")
        if not things and not self.text:
            raise RequestError("Add something to throw, or describe it.")
        if not self.text and all(n <= 1 for n in counts.values()):
            return Tier.PRESET
        return Tier.REMIX if things else Tier.NOVEL

    def to_dicts(self):
        return [p.to_dict() for p in self.parts]

    def expand(self, catalog):
        """The parts with each ingredient's full catalog entry attached under "block"."""
        return [
            {**p.to_dict(), "block": catalog.table(p.kind)[p.id]} if isinstance(p, Ingredient) else p.to_dict()
            for p in self.parts
        ]


def resolve_preset(recipe, catalog):
    """Build the full spell definition for a PRESET recipe from the catalog alone.

    An asset or motion block overrides the matching half of a preset spell; with no spell block,
    an asset alone flies with the default motion.
    """
    spell, asset, motion = (next(iter(recipe.ingredients(k)), None) for k in KINDS)
    base = catalog.spells[spell.id] if spell else None
    asset_id = asset.id if asset else base["asset"]
    motion_id = motion.id if motion else (base["motion"] if base else catalog.default_motion)
    a, m = catalog.assets[asset_id], catalog.motions[motion_id]
    if base and (asset_id, motion_id) == (base["asset"], base["motion"]):
        name, incantation, power = base["name"], base["incantation"], base["power"]
    else:
        name, incantation, power = f"{m['adjective']} {a['label']}", a["stem"] + m["suffix"], a["power"] + m["power"]
    return {
        "name": name,
        "incantation": incantation,
        "power": power,
        "channel_ms": catalog.channel_ms,
        "asset": dict(a),
        "motion": dict(m),
    }


HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
INCANTATION = re.compile(r"^[A-Za-z][A-Za-z'-]{1,19}$")


def validate_spell(spell, catalog):
    """Check and normalize a spell definition before it goes to the headset.

    Returns (spell, None) with defaults filled in and numbers clamped, or (None, error).
    Required: name, incantation (one word), asset.shape (a renderable shape), asset.color (#rrggbb),
    motion.id (a motion the runtime knows). Unknown extra fields are kept and passed through.
    """
    if not isinstance(spell, dict):
        return None, "spell must be a dict"
    name, incantation = spell.get("name"), spell.get("incantation")
    if not isinstance(name, str) or not name.strip() or len(name) > 40:
        return None, "name must be a non-empty string of at most 40 characters"
    if not isinstance(incantation, str) or not INCANTATION.match(incantation):
        return None, "incantation must be one word of 2-20 letters"
    asset, motion = spell.get("asset"), spell.get("motion")
    if not isinstance(asset, dict) or asset.get("shape") not in catalog.shapes:
        return None, f"asset.shape must be one of {catalog.shapes}"
    if not isinstance(asset.get("color"), str) or not HEX_COLOR.match(asset["color"]):
        return None, "asset.color must be a hex color like #ff6a1f"
    if not isinstance(motion, dict) or motion.get("id") not in catalog.motions:
        return None, f"motion.id must be one of {sorted(catalog.motions)}"

    def number(value, default, lo, hi, cast=float):
        try:
            return cast(min(hi, max(lo, float(value))))
        except (TypeError, ValueError):
            return default

    base_motion = catalog.motions[motion["id"]]
    return {
        **spell,
        "name": name.strip(),
        "power": number(spell.get("power"), 1, 0, 5, int),
        "channel_ms": number(spell.get("channel_ms"), catalog.channel_ms, 500, 6000, int),
        "asset": {**asset, "size": number(asset.get("size"), 0.3, 0.05, 1.5), "glow": bool(asset.get("glow", False))},
        "motion": {**base_motion, **motion, "speed": number(motion.get("speed"), base_motion["speed"], 0.5, 5)},
    }, None


_ids = itertools.count(1)


@dataclass
class SpellRequest:
    member_id: str
    recipe: Recipe
    tier: Tier
    id: str = field(default_factory=lambda: f"s{next(_ids)}")
    status: Status = Status.QUEUED
    created_at: float = field(default_factory=time.time)
    spell: dict = None     # everything the headset needs to show it, once resolved
    error: str = None
    attempts: int = 0      # times the forge has claimed it
    cast_at: float = None  # engine clock time the headset confirmed it is in the scene

    def to_public(self, ahead=None):
        out = {
            "id": self.id,
            "tier": self.tier.name.lower(),
            "status": self.status.value,
            "parts": self.recipe.to_dicts(),
            "created_at": self.created_at,
        }
        if ahead is not None:
            out["ahead"] = ahead
        if self.spell:
            out["spell"] = {"name": self.spell["name"], "incantation": self.spell["incantation"]}
        if self.error:
            out["error"] = self.error
        return out
