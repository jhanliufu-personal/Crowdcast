---
backend: claude-cli
slots: 4
scene_max_slots: 1
scene_max_attempts: 3
scene_ttl_s: 60
scene_timeout_s: 90
usage_limit_pause_s: 300
library_candidates: 5
match_score: 2
time_margin_s: 1.5
repair_min_s: 6
---
# Orchestrator

The orchestrator is code (`harness/forge.py`, `harness/scene.py`, `harness/pool.py`). It does not call a model to make
decisions. These settings (above) and this page describe what it does. The workers read the files in `workers/`.

## Rules, in order of priority

1. **Speed first.** A spell that is acceptable and fast is better than a perfect spell that is late. Every job has a
   time limit (the engine's lease). The orchestrator never starts a step that cannot finish in the time that remains.
2. **Reuse before you build.** Other models already paid for the items in `assets/`. Use them. Write new code only
   when nothing in the library is applicable.
3. **The smallest change.** Adapt the parameters of an item that exists before you combine items. Combine items
   before you write new code.
4. **One call for each job.** One model call makes the result. A second call is permitted only to repair a result
   that failed a check, and only if enough time remains (`repair_min_s`).

## The reuse ladder (spells)

The orchestrator tries each step in this order and stops at the first step that applies:

| Step | When | Worker | Model call |
|---|---|---|---|
| 0. exact | The library has a spell for the same recipe (same blocks, same words). | none | none |
| 1. adapt | REMIX request: it names a catalog spell or asset. | `spell-adapt` | fast, no code |
| 2. combine | NOVEL request, and a library object, model or spell matches a word of the request (score at least `match_score`). | `spell-combine` | fast, no code |
| 3. new | NOVEL request with no match. | `spell-new` | stronger; may write code |

The library search is code: it compares the words and tags of the request with the ids, tags and descriptions in
`assets/index.json` and `assets/generated/index.json`. The worker gets only the best `library_candidates` items.

After a step 3 result passes the code check, the new items go into `assets/generated/` with the status `candidate`.
They become `verified` when the spell runs on the headset with no error, and `quarantined` after an error.
Only `verified` and `prebuilt` items are offered to later requests.

## When a step fails

- The output fails a check (schema, catalog, code check): one repair call with the errors, if time remains.
  Otherwise drop the new code and use the shape fallback (level 1). The spell still flies.
- The model fails or times out: `nack` the job. The engine tries the request one more time.
- On the last attempt (`MAX_ATTEMPTS` in `game/engine.py`), or when the usage limit is reached: `ack` the nearest
  preset spell from the catalog. The sky never goes quiet.
- Usage limit: all slots stop for `usage_limit_pause_s`. The forge reports `capacity = 0` and `online = false`.

## Slots

`slots` model calls can run at the same time. Scene edits use at most `scene_max_slots`; spells get the rest.

## Scene edits

1. A request goes into the scene queue. Priority = the sum of its tag weights, plus one point for each second it waits.
   A request that waits longer than `scene_ttl_s` is dropped.
2. Requests that name the same existing object do not run at the same time (name locks).
3. Before the commands go into the edit log, the orchestrator checks if another edit changed one of the same objects
   after the worker read the scene. If yes, it makes the commands again (at most `scene_max_attempts` attempts).
