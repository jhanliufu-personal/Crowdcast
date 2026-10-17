"""The building blocks the audience composes spells from (shared/catalog.json).

Every block in the catalog is fully defined: an asset the headset can already render, a motion the
runtime already knows, or a preset spell made of one of each. Anything built only from these
needs no LLM.
"""

import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent.parent / "shared" / "catalog.json"
KINDS = ("spell", "asset", "motion")


class Catalog:
    def __init__(self, data):
        self.data = data
        self.assets = {a["id"]: a for a in data["assets"]}
        self.motions = {m["id"]: m for m in data["motions"]}
        self.spells = {s["id"]: s for s in data["spells"]}
        self.default_motion = data["default_motion"]
        self.channel_ms = data["channel_ms"]
        for s in self.spells.values():
            if s["asset"] not in self.assets or s["motion"] not in self.motions:
                raise ValueError(f"catalog spell {s['id']!r} uses an unknown asset or motion")
        if self.default_motion not in self.motions:
            raise ValueError(f"unknown default_motion {self.default_motion!r}")

    @classmethod
    def load(cls, path=CATALOG_PATH):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def table(self, kind):
        return {"spell": self.spells, "asset": self.assets, "motion": self.motions}[kind]

    def has(self, kind, block_id):
        return kind in KINDS and block_id in self.table(kind)
