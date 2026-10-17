"""The asset library: prebuilt items (assets/index.json, committed) and what models built (assets/generated/).

Item types: object (code that builds the thing to throw), model (a glTF file), behavior (code for an effect),
spell (a finished spell definition, found again by its recipe key).

Statuses:
    prebuilt     made by a person; offered to every request
    candidate    a model made it and it passed the code check; only reused for the same recipe (step 0)
    verified     it ran on the headset with no error; offered to every request
    quarantined  it caused an error on a page; never offered again

Generated files are content-addressed (the hash is in the id), so the same code is never stored twice.
"""

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from . import codecheck

ROOT = Path(__file__).resolve().parent.parent / "assets"
OFFERED = ("prebuilt", "verified")
STOPWORDS = set(
    "a an the and or of to in on at with into onto from that this these those it its is are be was were "
    "which who what when where while like as by for than then very really so too some any my your their "
    "make makes made turn turns become becomes spell spells thing things one two".split()
)
DIRS = {"object": "objects", "behavior": "behaviors", "spell": "spells"}


def words(text):
    """Content words, lowercased, with a plain plural "s" removed."""
    out = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in STOPWORDS or len(w) < 2:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def slug(text, limit=32):
    return re.sub(r"[^a-z0-9]+", "-", (text or "item").lower()).strip("-")[:limit] or "item"


class Library:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.generated = self.root / "generated"
        self._lock = threading.RLock()
        self.items = {}
        prebuilt = json.loads((self.root / "index.json").read_text(encoding="utf-8"))["items"]
        for item in prebuilt:
            self.items[item["id"]] = {**item, "status": "prebuilt", "source": "prebuilt", "uses": 0, "errors": 0}
        index = self.generated / "index.json"
        if index.exists():
            for item in json.loads(index.read_text(encoding="utf-8"))["items"]:
                self.items.setdefault(item["id"], item)

    # --- reading ---

    def get(self, item_id):
        with self._lock:
            item = self.items.get(item_id)
            return dict(item) if item else None

    def code(self, item_id):
        """The checked code of an object or behavior (with runtime guards), or None if it fails the check."""
        item = self.get(item_id)
        if not item or item["type"] not in ("object", "behavior") or item["status"] == "quarantined":
            return None
        ok, result = codecheck.check((self.root / item["file"]).read_text(encoding="utf-8"), item["type"])
        return result if ok else None

    def spell(self, item_id):
        item = self.get(item_id)
        if not item or item["type"] != "spell":
            return None
        return json.loads((self.root / item["file"]).read_text(encoding="utf-8"))

    def find_spell(self, recipe_key):
        """Step 0: a stored spell for exactly this recipe (candidates count here too)."""
        with self._lock:
            for item in self.items.values():
                if item["type"] == "spell" and item.get("recipe_key") == recipe_key and item["status"] != "quarantined":
                    return dict(item)
        return None

    def search(self, text, tags=(), types=("object", "model", "behavior", "spell"), limit=5):
        """The best offered items for a request: (score, item) pairs, best first."""
        want = words(text) | {t.lower() for t in tags}
        scored = []
        with self._lock:
            for item in self.items.values():
                if item["type"] not in types or item["status"] not in OFFERED:
                    continue
                item_words = words(item["id"].replace("-", " ")) | words(" ".join(item.get("tags", [])))
                desc_words = words(item.get("description", ""))
                score = 2 * len(want & item_words) + len(want & (desc_words - item_words))
                if not score:
                    continue
                score += 0.5 * (item["status"] == "verified") + 0.1 * min(item.get("uses", 0), 5)
                scored.append((score, dict(item)))
        scored.sort(key=lambda s: (-s[0], s[1]["id"]))
        return scored[:limit]

    def behaviors(self):
        """Every offered behavior (the list is short, so workers always see all of it)."""
        with self._lock:
            return [dict(i) for i in sorted(self.items.values(), key=lambda i: i["id"])
                    if i["type"] == "behavior" and i["status"] in OFFERED]

    def usable(self, item_id, types):
        item = self.get(item_id)
        return bool(item) and item["type"] in types and item["status"] != "quarantined"

    # --- writing ---

    def add_code(self, kind, proposed_id, code, description, tags, params, fallback_shape=None):
        """Store a new object or behavior as a candidate. Returns its id (an existing one for identical code)."""
        digest = hashlib.sha256(code.encode()).hexdigest()[:8]
        description = " ".join(str(description).split())[:200]  # one line: it becomes a // comment in the file
        with self._lock:
            for item in self.items.values():
                if item.get("hash") == digest and item["type"] == kind:
                    return item["id"]
            item_id = f"{slug(proposed_id, 24)}-{digest}"
            path = self.generated / DIRS[kind] / f"{item_id}.js"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"// {description}\n{code}\n", encoding="utf-8")
            self.items[item_id] = {
                "id": item_id, "type": kind, "file": str(path.relative_to(self.root)), "description": description,
                "tags": [slug(t, 20) for t in tags][:6], "params": params, "fallback_shape": fallback_shape,
                "status": "candidate", "source": "generated", "hash": digest, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "uses": 0, "errors": 0,
            }
            if kind == "object":
                self.items[item_id]["fallback_shape"] = fallback_shape or "sphere"
            else:
                self.items[item_id].pop("fallback_shape")
            self._save()
            return item_id

    def add_spell(self, recipe_key, spell, description, tags):
        body = json.dumps(spell, sort_keys=True)
        digest = hashlib.sha256(f"{recipe_key}\0{body}".encode()).hexdigest()[:8]
        with self._lock:
            existing = self.find_spell(recipe_key)
            if existing:
                return existing["id"]
            item_id = f"{slug(spell.get('name'), 24)}-{digest}"
            path = self.generated / "spells" / f"{item_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(spell, indent=2), encoding="utf-8")
            self.items[item_id] = {
                "id": item_id, "type": "spell", "file": str(path.relative_to(self.root)), "description": description,
                "tags": sorted(set(tags))[:8], "recipe_key": recipe_key, "status": "candidate", "source": "generated",
                "hash": digest, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "uses": 0, "errors": 0,
            }
            self._save()
            return item_id

    def used(self, item_ids):
        """The items ran on the headset: count the use, and promote candidates to verified."""
        with self._lock:
            changed = False
            for item_id in item_ids:
                item = self.items.get(item_id)
                if not item:
                    continue
                item["uses"] = item.get("uses", 0) + 1
                if item["status"] == "candidate":
                    item["status"] = "verified"
                changed |= item["source"] == "generated"
            if changed:
                self._save()

    def failed(self, item_id, error):
        """An item caused an error on a page: quarantine it if a model made it."""
        with self._lock:
            item = self.items.get(item_id)
            if not item:
                return
            item["errors"] = item.get("errors", 0) + 1
            item["last_error"] = str(error)[:300]
            if item["source"] == "generated":
                item["status"] = "quarantined"
                self._save()

    def _save(self):
        generated = [i for i in self.items.values() if i["source"] == "generated"]
        self.generated.mkdir(parents=True, exist_ok=True)
        tmp = self.generated / "index.tmp"
        tmp.write_text(json.dumps({"items": generated}, indent=1), encoding="utf-8")
        os.replace(tmp, self.generated / "index.json")

    def counts(self):
        with self._lock:
            out = {}
            for item in self.items.values():
                key = f"{item['type']}:{item['status']}"
                out[key] = out.get(key, 0) + 1
            return out
