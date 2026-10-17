"""The LLM forge: makes REMIX and NOVEL spells for the game engine (the contract is in game/forge.py).

For each job it climbs the reuse ladder in harness/orchestrator.md: exact library match, adapt, combine, new.
It always answers the job exactly once: ack with a spell, nack (the engine tries once more), or, on the last
attempt or at the usage limit, ack the nearest catalog preset so the sky never goes quiet.
"""

import collections
import json
import logging
import re
import threading

from game.engine import MAX_ATTEMPTS
from game.spell_request import INCANTATION, Ingredient, Recipe, resolve_preset, validate_spell

from . import backends, codecheck
from .library import words

log = logging.getLogger("harness.forge")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
SHIELD_WORD = "aegis"


class ForgeError(Exception):
    """The answer didn't pass a check."""


def recipe_key(brief):
    """Same blocks in the same order plus the same words: the same spell (step 0)."""
    blocks = [f"{p['kind']}:{p['id']}" for p in brief["parts"] if p["type"] == "ingredient"]
    return "|".join(blocks) + "|" + " ".join(re.findall(r"[a-z0-9]+", brief["text"].lower()))


def recipe_tags(brief, catalog):
    tags = set()
    for p in brief["parts"]:
        if p["type"] != "ingredient":
            continue
        block = p["block"]
        if p["kind"] == "spell":
            block = catalog.assets.get(block.get("asset"), {})
        tags.update(block.get("tags", []))
    return sorted(tags)


class LLMForge:
    def __init__(self, harness):
        self.h = harness
        self.incantations = collections.deque(maxlen=30)  # recently acked, so new ones sound different

    @property
    def capacity(self):
        return self.h.pool.spell_capacity()

    @property
    def online(self):
        return not self.h.pool.paused()

    def submit(self, job):
        threading.Thread(target=self._run, args=(job,), name=f"forge-{job.request.id}", daemon=True).start()

    # --- one job ---

    def _run(self, job):
        brief = job.brief()
        job_id = f"{brief['request_id']}#{brief['attempt']}"
        pool = self.h.pool
        pool.start(job_id, "spell", tier=brief["tier"])
        info = {}
        try:
            spell, info = self._make(job, brief, job_id)
            outcome = "acked" if self._ack(job, spell) else "stale"
        except backends.UsageLimitError as err:
            pool.pause(err.pause_s or self.h.files.setting("usage_limit_pause_s", 300), str(err)[:120])
            spell, info = self._fallback(job, brief, f"usage limit: {err}")
            outcome = "acked" if self._ack(job, spell) else "stale"
        except Exception as err:  # BackendError, ForgeError, or a bug: the job is still answered
            if not isinstance(err, (backends.BackendError, ForgeError)):
                log.exception("forge bug on %s", job_id)
            if job.lease.attempt >= MAX_ATTEMPTS:
                spell, info = self._fallback(job, brief, str(err))
                outcome = "acked" if self._ack(job, spell) else "stale"
            else:
                info = {**info, "error": str(err)[:200]}
                outcome = "nacked" if job.nack(str(err)[:200]) else "stale"
        pool.finish(job_id, outcome, **info)

    def _ack(self, job, spell):
        if not job.is_current():
            return False
        ok = job.ack(spell)
        if ok:
            self.incantations.append(spell["incantation"])
        return ok

    def _make(self, job, brief, job_id):
        files, lib, catalog = self.h.files.fresh(), self.h.library, job.catalog
        key, tags = recipe_key(brief), recipe_tags(brief, catalog)

        hit = lib.find_spell(key)
        if hit:
            spell = lib.spell(hit["id"])
            spell["library"] = {**spell.get("library", {}), "spell": hit["id"]}
            spell["harness"] = {"step": "exact"}
            return spell, {"step": "exact", "reused": hit["id"]}

        limit = int(files.setting("library_candidates", 5))
        candidates = lib.search(brief["text"], tags, limit=limit)
        if brief["tier"] == "remix":
            worker_name, step = "spell-adapt", "adapt"
        elif any(s >= files.setting("match_score", 2) and i["type"] in ("object", "model", "spell") for s, i in candidates):
            worker_name, step = "spell-combine", "combine"
        else:
            worker_name, step = "spell-new", "new"
        worker = files.worker(worker_name)
        self.h.pool.update(job_id, step=step, worker=worker_name, model=worker.meta.get("model"))
        info = {"step": step, "worker": worker_name, "model": worker.meta.get("model")}

        system = self._system(files, worker, catalog)
        prompt = self._prompt(brief, tags, candidates, files)
        schema = files.output_schema(worker.meta["output"], catalog)
        answer = self._call(files, worker, system, prompt, schema, job)
        try:
            spell, problems = self._build(answer, worker, catalog)
        except ForgeError as err:
            spell, problems = None, [str(err)]
        if problems and job.seconds_left() >= files.setting("repair_min_s", 6):
            info["repaired"] = True
            repair = (f"{prompt}\n\n## Your previous answer\n{json.dumps(answer)}\n\n## Problems in it\n"
                      + "\n".join(f"- {p}" for p in problems) + "\n\nAnswer again with these problems fixed.")
            answer = self._call(files, worker, system, repair, schema, job)
            spell, problems = self._build(answer, worker, catalog)
        if spell is None:
            raise ForgeError("; ".join(problems))
        if problems:
            info["dropped"] = problems[:3]  # new code that failed: the spell flies without it

        spell["harness"] = {"step": step, "worker": worker_name}
        used = [x for x in [spell["asset"].get("object", {}).get("id")] + [b["id"] for b in spell["behaviors"]] if x]
        spell["library"] = {"items": used}
        spell_id = lib.add_spell(key, spell, f"{spell['name']}: {brief['text'] or 'catalog blocks'}", tags + list(words(brief["text"])))
        spell["library"]["spell"] = spell_id
        return spell, info

    def _call(self, files, worker, system, prompt, schema, job):
        timeout = job.seconds_left() - float(files.setting("time_margin_s", 1.5))
        if not job.is_current():
            raise ForgeError("the job is no longer current")
        answer, meta = backends.call(
            self.h.backend,
            system=system, prompt=prompt, schema=schema, model=worker.meta.get("model", "claude-haiku-5-5"),
            effort=worker.meta.get("effort", "low"), max_tokens=int(worker.meta.get("max_tokens", 2000)), timeout=timeout,
        )
        return answer

    # --- prompts ---

    def _system(self, files, worker, catalog):
        """Stable across requests for one worker, so the API backend can cache it."""
        parts = [files.policy, f"# Your task\n\n{worker.body}", f"# Answer format\n\n{files.output_doc(worker.meta['output'])}"]
        if worker.meta.get("code_api"):
            parts.append(files.spell_api)
        motions = "\n".join(f"- {m['id']}: {m['description']} (default speed {m['speed']})" for m in catalog.motions.values())
        assets = "\n".join(
            f"- {a['id']}: {a['label']}, shape {a['shape']}, color {a['color']}, size {a['size']}, glow {str(a['glow']).lower()}, "
            f"stem \"{a['stem']}\", tags {', '.join(a.get('tags', []))}" for a in catalog.assets.values()
        )
        spells = "\n".join(
            f"- {s['id']}: \"{s['name']}\" ({s['incantation']}), asset {s['asset']}, motion {s['motion']}, power {s['power']}"
            for s in catalog.spells.values()
        )
        parts.append(
            f"# Catalog\n\nShapes: {', '.join(catalog.shapes)}\n\nMotions:\n{motions}\n\nAssets:\n{assets}\n\nPreset spells:\n{spells}"
        )
        return "\n\n".join(parts)

    def _prompt(self, brief, tags, candidates, files):
        lib = self.h.library
        lines = ["## Request", json.dumps({"tier": brief["tier"], "text": brief["text"], "parts": brief["parts"]})]
        lines.append("\n## Library candidates (matched to the request; best first)")
        if candidates:
            for score, item in candidates:
                entry = {k: item.get(k) for k in ("id", "type", "description", "tags", "params")}
                if item["type"] == "spell":
                    s = lib.spell(item["id"]) or {}
                    entry["spell"] = {k: s.get(k) for k in ("name", "asset", "motion", "behaviors", "power")}
                lines.append(json.dumps({k: v for k, v in entry.items() if v not in (None, [], {})}))
        else:
            lines.append("(none matched)")
        lines.append("\n## Library behaviors (all of them)")
        lines += [json.dumps({"id": b["id"], "description": b["description"], "params": b.get("params", {})})
                  for b in sorted(lib.behaviors(), key=lambda b: -b.get("uses", 0))[:20]]
        notes = [files.tags[t].body for t in tags if t in files.tags and files.tags[t].meta.get("applies_to") in ("spell", "both")]
        if notes:
            lines.append("\n## Notes for this request's tags")
            lines += [f"- {n}" for n in notes]
        if self.incantations:
            lines.append(f"\n## Incantations in use (choose a different one)\n{', '.join(self.incantations)}")
        return "\n".join(lines)

    # --- checking an answer ---

    def _build(self, answer, worker, catalog):
        """Turn a worker's answer into an engine spell. Returns (spell, problems).

        problems lists what a repair call could fix. New code that fails its check is dropped (and listed), and the
        spell uses the nearest shape instead; only an answer that can't make any spell raises ForgeError.
        """
        lib, problems, notes = self.h.library, [], []  # problems: worth a repair call; notes: fixed here
        if not isinstance(answer, dict):
            raise ForgeError("the answer is not an object")

        new_ids = {}  # proposed id -> stored library id
        if worker.meta.get("code_api"):
            new_object = answer.get("new_object")
            entries = ([("object", new_object)] if isinstance(new_object, dict) else []) + [
                ("behavior", b) for b in (answer.get("new_behaviors") or [])[:2] if isinstance(b, dict)
            ]
            for kind, entry in entries:
                code = entry.get("code") or ""
                ok, result = codecheck.check(code, kind)
                if not ok:
                    problems.append(f"new {kind} {entry.get('id')!r} failed the code check: " + "; ".join(result))
                    continue
                stored = lib.add_code(
                    kind, entry.get("id", kind), code, entry.get("description", ""), entry.get("tags") or [],
                    parse_params(entry.get("params_json")), entry.get("fallback_shape"),
                )
                new_ids[entry.get("id")] = stored
            fallback_shapes = {new_object.get("id"): new_object.get("fallback_shape")} if isinstance(new_object, dict) else {}
        else:
            fallback_shapes = {}

        name = " ".join(str(answer.get("name") or "").split())[:40] or "Mystery Spell"
        incantation = str(answer.get("incantation") or "").strip()
        if not INCANTATION.match(incantation) or incantation.lower() == SHIELD_WORD:
            letters = re.sub(r"[^A-Za-z]", "", name)[:12]
            incantation = (letters or "Mysto").capitalize()
            notes.append("incantation replaced")

        color = str(answer.get("color") or "")
        if re.fullmatch(r"#[0-9a-fA-F]{3}", color):
            color = "#" + "".join(c * 2 for c in color[1:])
        if not HEX.match(color):
            notes.append(f"color {color!r} replaced")
            color = "#cccccc"

        motion = answer.get("motion")
        if motion not in catalog.motions:
            notes.append(f"motion {motion!r} replaced")
            motion = catalog.default_motion

        # The thing to throw.
        ref = str(answer.get("object") or "")
        ref = new_ids.get(ref, ref)
        asset = {"color": color, "size": clamp(answer.get("size"), 0.1, 1.2, 0.35), "glow": bool(answer.get("glow")), "label": name}
        if ref.startswith("shape:") and ref[6:] in catalog.shapes:
            asset["shape"] = ref[6:]
        elif lib.usable(ref, ("object", "model")):
            item = lib.get(ref)
            asset["shape"] = item.get("fallback_shape") or "sphere"
            asset["object"] = {"id": ref, "params": {**item.get("params", {}), **parse_params(answer.get("object_params_json"))}}
        else:
            if ref and ref not in fallback_shapes:
                notes.append(f"unknown object {ref!r} replaced by a shape")
            asset["shape"] = fallback_shapes.get(answer.get("object")) or "sphere"
            if asset["shape"] not in catalog.shapes:
                asset["shape"] = "sphere"

        behaviors = []
        for b in (answer.get("behaviors") or [])[:3]:
            bid = new_ids.get(b.get("id"), b.get("id")) if isinstance(b, dict) else None
            if bid and lib.usable(bid, ("behavior",)):
                behaviors.append({"id": bid, "params": {**lib.get(bid).get("params", {}), **parse_params(b.get("params_json"))}})
            elif bid:
                notes.append(f"unknown behavior {bid!r} dropped")

        spell = {
            "name": name,
            "incantation": incantation,
            "power": int(clamp(answer.get("power"), 1, 3, 1)),
            "asset": asset,
            "motion": {"id": motion, "speed": clamp(answer.get("speed"), 0.8, 4, catalog.motions[motion]["speed"])},
            "behaviors": behaviors,
        }
        if answer.get("target") in ("egg", "user"):
            spell["target"] = answer["target"]  # used when the catalog's targeting mode is "harness"
        if answer.get("flavor"):
            spell["flavor"] = " ".join(str(answer["flavor"]).split())[:160]
        _, error = validate_spell(spell, catalog)
        if error:
            raise ForgeError(error)
        if notes:
            spell["notes"] = notes[:5]
        return spell, problems

    # --- fallback ---

    def _fallback(self, job, brief, reason):
        """The nearest catalog preset: the request's own blocks, else the asset whose tags match its words."""
        catalog = job.catalog
        chosen = {}
        for p in brief["parts"]:
            if p["type"] == "ingredient":
                chosen.setdefault(p["kind"], p["id"])
        if "spell" not in chosen and "asset" not in chosen:
            want = words(brief["text"])
            best = max(catalog.assets.values(), key=lambda a: (len(want & set(a.get("tags", []) + [a["id"]])), -len(a["id"])))
            chosen["asset"] = best["id"]
        recipe = Recipe(tuple(Ingredient(kind, chosen[kind]) for kind in ("spell", "asset", "motion") if kind in chosen))
        spell = resolve_preset(recipe, catalog)
        spell["harness"] = {"step": "fallback", "reason": reason[:200]}
        return spell, {"step": "fallback", "error": reason[:200]}


def parse_params(text):
    try:
        value = json.loads(text) if isinstance(text, str) and text.strip() else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def clamp(value, lo, hi, default):
    try:
        return min(hi, max(lo, float(value)))
    except (TypeError, ValueError):
        return default
