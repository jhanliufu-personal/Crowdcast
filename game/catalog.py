"""The building blocks the audience composes spells from (shared/catalog.json).

Every block in the catalog is fully defined: an asset the headset can already render, a motion the
runtime already knows, or a preset spell made of one of each. Anything built only from these
needs no LLM.
"""

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent.parent / "shared" / "catalog.json"
KINDS = ("spell", "asset", "motion")
TARGETS = ("egg", "user")  # what a spell can fly at
TARGET_MODES = ("harness", "mix", "egg", "user")


class Catalog:
    def __init__(self, data):
        self.data = data
        self.assets = {a["id"]: a for a in data["assets"]}
        self.motions = {m["id"]: m for m in data["motions"]}
        self.spells = {s["id"]: s for s in data["spells"]}
        self.default_motion = data["default_motion"]
        self.channel_ms = data["channel_ms"]
        self.preset_channel_ms = data.get("preset_channel_ms", self.channel_ms)  # instant spells charge up faster
        self.shapes = data["shapes"]  # what the headset can render today (makeShape in index.html)
        # Who spells fly at. mode: "harness" (the forge picks per spell; others are random by egg_share),
        # "mix" (always random by egg_share), "egg" or "user" (every spell).
        self.targeting = {"mode": "harness", "egg_share": 0.5, **data.get("targeting", {})}
        if self.targeting["mode"] not in TARGET_MODES:
            raise ValueError(f"targeting.mode must be one of {TARGET_MODES}")
        # The player in the headset: a hit costs damage_per_power × the spell's power; heal_per_s restores it.
        self.player = {"max_hp": 100, "damage_per_power": 10, "heal_per_s": 1, **data.get("player", {})}
        for s in self.spells.values():
            if s["asset"] not in self.assets or s["motion"] not in self.motions:
                raise ValueError(f"catalog spell {s['id']!r} uses an unknown asset or motion")
        for a in self.assets.values():
            if a["shape"] not in self.shapes:
                raise ValueError(f"catalog asset {a['id']!r} has unknown shape {a['shape']!r}")
        if self.default_motion not in self.motions:
            raise ValueError(f"unknown default_motion {self.default_motion!r}")

    @classmethod
    def load(cls, path=CATALOG_PATH):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def table(self, kind):
        return {"spell": self.spells, "asset": self.assets, "motion": self.motions}[kind]

    def has(self, kind, block_id):
        return kind in KINDS and block_id in self.table(kind)
