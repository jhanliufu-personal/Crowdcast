"""The harness: runs Claude workers in parallel for spells (the game's LLM forge) and for scene edits.

The rules live in Markdown files next to this one (see README.md); this package is the code that applies them.

    harness = Harness(hub, catalog)                  # hub: the scene hub in serve.py
    engine = GameEngine(catalog, events, llm_forge=harness.forge)
    harness.attach(engine)                           # starts the cast bridge and the scene queue

The backend is WORLDTINDER_BACKEND if set, else the "backend" setting in orchestrator.md (claude-cli by default).
"""

import os

from .bridge import CastBridge
from .forge import LLMForge
from .library import Library
from .loader import HarnessFiles
from .pool import Pool
from .scene import SceneQueue


class Harness:
    def __init__(self, hub, catalog, files=None, library=None):
        self.hub = hub
        self.catalog = catalog
        self.files = files or HarnessFiles()
        self.library = library or Library()
        self.pool = Pool(self.files)
        self.forge = LLMForge(self)
        self.scene = SceneQueue(self, hub)
        self.bridge = None

    @property
    def backend(self):
        return os.environ.get("WORLDTINDER_BACKEND") or self.files.fresh().setting("backend", "claude-cli")

    def attach(self, engine):
        self.bridge = CastBridge(self, self.hub, engine)
        self.bridge.start()
        self.scene.start()

    def stop(self):
        self.scene.stop()
        if self.bridge:
            self.bridge.stop()

    def status(self):
        return {
            "backend": self.backend,
            **self.pool.status(),
            "scene": self.scene.status(),
            "library": self.library.counts(),
        }
