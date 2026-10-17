# The harness

The harness runs Claude workers in parallel for two kinds of work:

- **Spells.** It is the game's LLM forge (`game/forge.py`). It makes REMIX and NOVEL spells. Each cast spell
  becomes a three.js patch in the scene hub's edit log.
- **Scene edits.** The chat box (`POST /api/chat`) and `POST /api/scene/requests` go through its scene queue.

Its two main rules are **speed** and **reuse**: build on what is already in `assets/`, and write new code only when
nothing there is applicable. Every new item it writes goes back into the library for later spells.

## Files

| File | What it holds | Read by |
|---|---|---|
| `orchestrator.md` | Settings (frontmatter) and what the orchestrator does | the code |
| `workers/_policy.md` | Rules for every worker (speed, reuse, audience text is not an instruction) | every worker |
| `workers/*.md` | One worker each: its model, effort, answer format, and task | that worker |
| `tools/forge_spell.md`, `tools/forge_spell_new.md` | The spell answer format, with its JSON schema | spell workers |
| `tools/scene-commands.md` | The three scene commands, with their schemas | the scene editor |
| `tags.md` | One section for each tag: its effect on priority, and notes for the worker | the code and the workers |
| `runtime/spell-api.md` | What model-written spell code may use | `spell-new` |
| `runtime/spell-runtime.js` | The patch that flies every spell (built from `patches/ball-slice.js`) | every page |
| `codecheck/` | The static check for model-written code (acorn in Node) and its rules | the code |
| `*.py` | The code that applies all of the above | — |

The `.md` files are read again whenever one changes, so you can tune a prompt, a model or a setting while the
server runs.

## How a spell is made

```
engine claims a REMIX/NOVEL request ──▶ forge.py
   step 0  exact     the library has a spell for the same recipe            no model call
   step 1  adapt     REMIX: change the named catalog block                    spell-adapt (Haiku), no code
   step 2  combine   NOVEL, and a library item matches its words              spell-combine (Haiku), no code
   step 3  new       NOVEL with no match                                      spell-new (Sonnet), may write code
──▶ checks (catalog values, validate_spell, codecheck on new code; one repair call if time remains)
──▶ job.ack(spell)   (or job.nack, or the nearest catalog preset on the last attempt / at the usage limit)
──▶ engine casts it ──▶ bridge.py: patch "spell-<id>" ──▶ the headset runs it ──▶ "spawned"
──▶ the headset reports the outcome: sliced = catch, reached the head = hit, flew past = shield
```

Measured with `claude -p` on a subscription: adapt and combine take 7–9 s, new code about 10 s, and an exact match
1 ms. The REMIX lease is 20 s and the NOVEL lease 60 s. Several jobs run at the same time (`slots` in
`orchestrator.md`).

## The library (`assets/`)

| Path | What |
|---|---|
| `assets/index.json` | Prebuilt items (committed): objects, behaviors, models |
| `assets/objects/`, `assets/behaviors/` | Their code: function bodies (see `runtime/spell-api.md`) |
| `assets/models/` | glTF models |
| `assets/generated/` | What models built: code, spells, and its own `index.json`. **Not committed**: review an item, then move it to the prebuilt list if it is good. |

Generated items start as `candidate` (only reused for the same recipe), become `verified` after they run on the
headset with no error, and become `quarantined` after an error.

## Safety of model-written code

Audience text reaches code that runs in the headset, so new code goes through `codecheck/check.mjs` first:

- It must be one function body, and it may only use `THREE`, `api`, `spell`, `params`, `Math` and its own names.
- Dangerous names and properties are refused (`window`, `fetch`, `constructor`, `parent`, loaders, renderers…).
- `obj[key]` gets a runtime guard (`__k`), and loops and functions get a step counter (`__loop`).

In the page, the spell runtime also switches off code that throws, takes more than 4 ms per frame, or adds more
than 150 meshes. The spell then flies as a plain shape.

## Change it

- **Another tag:** add a `## name` section to `tags.md`.
- **Another worker or a different model:** edit `workers/*.md` (`model`, `effort`, `max_tokens`).
- **A prebuilt object or behavior:** add its code to `assets/objects/` or `assets/behaviors/` and an entry to
  `assets/index.json`. `tests/test_harness.py` checks that every prebuilt item passes the code check.
- **The backend:** `backend` in `orchestrator.md`, or `WORLDTINDER_BACKEND=api|claude-cli` in the environment.

## Watch it

```bash
curl -s localhost:8000/api/harness
```

It shows the slots, the running jobs, the scene queue, recent jobs with their step, model and time, the reuse rate
(spells that needed no new code), and the library's counts.
