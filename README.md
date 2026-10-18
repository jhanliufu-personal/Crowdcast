# Midnight Egg 🥚✨

*A mixed-reality wizard duel for Meta Quest (WebXR) where the audience invents the spells.*

One player in a Quest headset stands in a castle hall guarding a magical egg until midnight. Everyone else in the room
plays from their phones: they compose spells from ready-made blocks or describe new ones in plain words, and an
**LLM scene agent turns every request into live code in the headset's scene**, in real time, while the round is
running. The player slices spells with a blade in the right hand, blocks them with a shield in the left, and tries to
keep the egg (and themselves) in one piece until the clock strikes twelve.

The repository is called **WorldTinder**; the game is **Midnight Egg**.

> **Theme note:** the style is classic wizarding-school fantasy (castle at night, wands, magical creatures), but every
> name, spell word and character is original, so the demo is safe to show, record and publish.

This README is written so that a person or a coding agent new to the project can pick it up and keep going. Part I
describes **what exists in the repo today and why it is built that way**. Part II is the **design target**: the full
game as planned, much of which is not built yet. When the two disagree, Part I is the truth about the code.

---

## Table of contents

**Part I: the system as built**

1. [Motivation: any single-player game, played by a crowd](#1-motivation-any-single-player-game-played-by-a-crowd)
2. [Status at a glance](#2-status-at-a-glance)
3. [Quick start](#3-quick-start)
4. [Architecture](#4-architecture)
5. [The life of one spell](#5-the-life-of-one-spell)
6. [Design decisions and why](#6-design-decisions-and-why)
7. [Components](#7-components)
8. [Game rules as implemented](#8-game-rules-as-implemented)
9. [Spell requests and the queue](#9-spell-requests-and-the-queue)
10. [Acknowledgements and the forge contract](#10-acknowledgements-and-the-forge-contract)
11. [Network protocol and API](#11-network-protocol-and-api)
12. [Running the demo in one room](#12-running-the-demo-in-one-room)
13. [Project structure](#13-project-structure)
14. [Working on this repo](#14-working-on-this-repo)
15. [Known gaps and next steps](#15-known-gaps-and-next-steps)

**Part II: the design target**

16. [Core idea](#16-core-idea)
17. [Game design](#17-game-design)
18. [Audience experience (target)](#18-audience-experience-target)
19. [Planned LLM jobs](#19-planned-llm-jobs)
20. [Voice and audio (ElevenLabs)](#20-voice-and-audio-elevenlabs)
21. [Safety and guardrails](#21-safety-and-guardrails)
22. [Latency budget](#22-latency-budget)
23. [MVP vs stretch goals](#23-mvp-vs-stretch-goals)
24. [Demo script](#24-demo-script)

---

# Part I: the system as built

## 1. Motivation: any single-player game, played by a crowd

VR games are lonely to watch: one person has all the fun and everyone else stares at a mirrored screen. The claim
behind this project is that **any single-player game can become a crowd game** if the crowd can change the world the
player is in, live, and an LLM does the work of turning each person's idea into something that runs in the scene.

Midnight Egg is the demonstration. The pieces that make the claim work are general, and most of the repo is built
that way:

| Reusable machinery (not specific to this game) | Specific to Midnight Egg |
|---|---|
| A scene hub that keeps an **ordered edit log** and syncs it to every page over a WebSocket (`serve.py`) | The castle hall, the egg, the HUD, the end-of-round effects (`index.html`) |
| **Patches**: named JavaScript snippets hot-loaded into the running scene | The spell runtime: flight paths, slicing, the shield (`harness/runtime/spell-runtime.js`) |
| **Structured audience requests** sorted into tiers by how much LLM work they need (`game/spell_request.py`) | The catalog of spells, things to throw and motions (`shared/catalog.json`) |
| A **priority queue with leases and acks**, so requests survive slow or failed LLM calls (`game/request_queue.py`) | The round: acts, cracks, player health, scoring (`game/engine.py`) |
| The **harness**: parallel LLM workers with a reuse-first policy, prompts in Markdown, a code checker, and a growing library (`harness/`) | The worker prompts and tags that describe spells (`harness/workers/`, `harness/tags.md`) |
| A phone web app, a host console and a QR join screen on one laptop over local Wi-Fi | Their wording, emoji and theme |

Requests that need no new code skip the LLM entirely. Requests that do are queued and handled in parallel, with the
wait hidden on the audience's phones ("brewing…"). The headset only ever sees finished spells.

## 2. Status at a glance

| Area | State |
|---|---|
| WebXR scene: castle hall, egg, 2×-scale player with gravity, jumping and climbing, desktop and VR controls | **Built** |
| Live sync: edit log, patches, headset → viewer state streaming, `?mirror` | **Built** |
| Audience app: join, prebuilt and custom tabs, spell status, score and rank | **Built** |
| Host console with a cauldron-line queue view, roster, scores, Start/Reset, and a 2D "player's view" | **Built** |
| Join screen (QR code) and control room (scene + host side by side) | **Built** |
| Request tiers, priority queue, leases, two-stage acks, act pacing | **Built** and unit-tested |
| LLM forge (the harness): exact reuse → adapt → combine → new code, with code checking | **Built** and unit-tested |
| Spells fly at the egg or at the player; slice = catch, shield = block, reach target = hit | **Built** |
| Egg cracks, player health with slow healing, HUD in the headset, hit feedback ("Ouch!") | **Built** |
| End of round: the egg shatters (broken) or hatches a dragon spilling gems (survived) | **Built** |
| Synthesized sound effects (including per-spell sound recipes from the LLM) and background music | **Built** |
| LAN play from phones, HTTPS for the Quest, WSL2 port-forward detection, Tailscale "public door" | **Built** |
| Return a caught spell by shouting its incantation | **Not built** (`return` exists only as a server API) |
| Voice (TTS chants, narrator, speech-to-text), ElevenLabs | **Not built** |
| Caster avatars (broomsticks) in the 3D scene, spectator view | **Not built** (the host's 2D player's view stands in) |
| Midnight merged giant spell, a hatched creature designed by the LLM from the caught spells | **Not built** (the hatch is a fixed dragon model) |
| Charge meter | Tracked by the engine, **not shown** anywhere anymore |

## 3. Quick start

No build step. Python 3.12 standard library, plus `anthropic` only if you use the API backend. Node.js is needed for
the code checker on model-written spell code; without it, new-code spells fly as plain shapes.

```bash
conda env create -f environment.yml   # Python 3.12 + the anthropic package
conda activate worldtinder
python serve.py                        # HTTP on 8000
python serve.py --https                # also HTTPS on 8443, for the Quest over Wi-Fi
python -m unittest discover tests      # 65 tests: game logic, harness, Quest forwarding
```

The server prints every address you need when it starts. The important ones:

| Who | URL |
|---|---|
| Phones (audience) | `http://<lan-ip>:8000/audience/` |
| Projector (QR code + joined count) | `http://localhost:8000/join/` |
| Host console | `http://localhost:8000/host/#key=<host key>` |
| Control room (scene + host side by side) | `http://localhost:8000/control/#key=<host key>` |
| Quest over Wi-Fi | `https://<lan-ip>:8443/#key=<host key>` (accept the certificate warning) |
| Quest over USB or wireless adb | `http://localhost:8000/` (the server keeps `adb reverse` set up) |

The host key is `WORLDTINDER_HOST_KEY`, or a random one printed at each start.

**Server flags:** `--https`, `--port` (8000), `--https-port` (8443), `--join-host` / `WORLDTINDER_JOIN_HOST` (the
address phones should use, when detection picks the wrong network), `--public-port` and `--public-url` (a tunnel
entrance, see [section 12](#12-running-the-demo-in-one-room)), `--targeting harness|mix|egg|user` (what spells fly
at), `--verbose` (log every request; by default only refusals and failures are logged).

**LLM settings** (details in [`harness/README.md`](harness/README.md)):

| Setting | Default | Meaning |
|---|---|---|
| `WORLDTINDER_BACKEND`, or `backend` in `harness/orchestrator.md` | `claude-cli` | `claude-cli` runs the Claude Code CLI headless (`claude -p`) on your subscription, with all tools and MCP servers disabled and any API key removed from its environment. `api` calls the Claude API with structured outputs and needs `ANTHROPIC_API_KEY`. |
| `model`, `effort`, `max_tokens` in `harness/workers/*.md` | `claude-opus-5-5`, effort `low`, for every worker | Per worker. The `claude-cli` backend needs Claude Code 2.1.280 or newer for these models (`claude update`). |
| `slots`, `scene_max_slots` in `harness/orchestrator.md` | 4, 1 | Claude calls running at once, and how many of them scene edits may use. |

Without any LLM backend working, preset spells still work: they never touch the LLM.

**Testing without a headset:** open `http://localhost:8000/?headset` in a desktop tab. That tab becomes the headset
(it plays the sounds, decides spell outcomes, and shows the HUD); flick the mouse across a spell to slice it.
`?emulate` loads Meta's IWER emulated Quest 3 so "Enter VR" works on a laptop (this is the one thing that needs
internet). Any other tab is a viewer of whatever the headset is doing.

## 4. Architecture

Everything runs in **one Python process on one laptop**. Phones, the Quest and the projector connect to it over the
room's Wi-Fi. Nothing is hosted online; only the LLM calls leave the machine.

```
 phones: audience/ ──POST /api/spells {parts}──▶ ┌────────────── serve.py (one process) ──────────────────┐
         ◀── SSE: me, spell:status, game:state ─ │                                                          │
                                                 │  game/  GameEngine                                       │
 host/, join/, control/ ◀── SSE: host:view,      │    members, scores, round, RequestQueue (tier, arrival)  │
         game:state, POST /api/admin/* ────────▶ │      │ claim (lease)                                     │
                                                 │      ├─ PRESET ─────────▶ CatalogForge  (no LLM, acks now)│
                                                 │      └─ REMIX / NOVEL ──▶ harness LLMForge ──▶ Claude     │
                                                 │                             (workers in parallel)        │
                                                 │      job.ack(spell) ──▶ ready ──(paced by act)──▶ cast    │
                                                 │                                                  │       │
                                                 │  harness/bridge.py  CastBridge ◀─────────────────┘       │
                                                 │      │ patch "spell-<id>" (+ "spell-runtime" once)        │
                                                 │      ▼                                                    │
                                                 │  scene hub: ordered edit log (scenes/live.json)           │
                                                 └──────┬─────────────────────────────────▲─────────────────┘
                                                        │ /ws: init (whole log), then ops │ pose 30/s, state 10/s,
                                                        ▼                                 │ ctx.report (spawned,
                     ┌──────────────── index.html ──────────────────┐                     │ outcomes), errors
                     │ headset (Quest, ?headset, ?emulate): runs    │─────────────────────┘
                     │ patches, decides outcomes, plays sound, HUD  │
                     │ viewers (any other tab, ?mirror): run the    │◀── headset state relayed by the server
                     │ same patches, copy the headset's state       │
                     └──────────────────────────────────────────────┘
```

**Who is the authority for what**

- The **server** owns the session: members, scores, the request queue, the round clock, cracks, player health. It
  never simulates spells.
- The **headset page** owns the simulation: where spells are, whether the blade sliced one, whether the shield
  blocked it, whether it reached its target. It reports outcomes to the server, which scores them.
- **Viewer pages** run the same edit log (so they build the same objects) and then move their copies to match the
  headset's streamed state. Spell flight is a deterministic function of the headset's clock and a seed, so viewers
  compute the same path without being told.
- The **host console's player's view** is a separate 2D drawing from game events. The server doesn't know where
  spells are, so the host page animates their paths itself from each spell's motion, speed and channel time.

**Threads in the process:** one per HTTP connection (`ThreadingHTTPServer`, including one per open SSE stream and
WebSocket), the engine's dispatch and tick loops, the cast bridge loop, the harness's worker pool, a headset
watcher, and an adb loop that keeps the Quest's `localhost:8000` forwarded. The engine is guarded by one re-entrant
lock; state is published at most every 0.25 s to the host and once a second (or on change) to everyone.

## 5. The life of one spell

1. **Compose.** On a phone, the audience member taps blocks (a spell, a thing to throw, a way to fly) and/or types
   text. The request is the ordered list of pieces, not a sentence (see [section 9](#9-spell-requests-and-the-queue)).
2. **Submit.** `POST /api/spells`. The server validates every piece against `shared/catalog.json`, applies the 5 s
   cooldown and the one-pending-spell rule, and sorts it into a tier: ⚡ preset, ✨ remix or 🔮 novel.
3. **Queue.** The `RequestQueue` serves presets first, then remixes, then novel requests, first come first served
   within a tier. LLM tiers are only claimed while the LLM forge has free capacity, so a later preset overtakes
   them.
4. **Forge.** A claim hands the request to a forge with a lease. Presets go to `CatalogForge`, which resolves them
   from the catalog and acks in microseconds. Remix and novel requests go to the harness, which climbs a reuse
   ladder: an exact match in the library (no call), adapt a catalog block (one call, no code), combine library items
   (one call, no code), or write new code (one call, then a static check and maybe one repair call). Measured with
   `claude -p`: 7–10 s for a model call, 1 ms for an exact match.
5. **Ack.** `job.ack(spell)` validates the spell and moves it to the ready stage; a failure or timeout puts the
   request back at its old position, and it fails after 2 attempts (the harness falls back to the nearest preset on
   the last attempt).
6. **Pace.** Ready spells are cast while the current act has room in the sky (dusk 2, storm 5, midnight 3). The
   engine decides the spell's target (the egg or the player) and adds the caster's name and color.
7. **Deliver.** The cast bridge puts a transient patch `spell-<id>` into the scene hub's edit log. Every page runs
   it; it calls the spell runtime with the spell and its library code.
8. **Fly.** The spell charges up, plays its sound, and flies its motion at its target. On the headset, the right-hand
   blade (or a mouse flick) slices it, the left-hand shield blocks it, or it reaches the egg or the player.
9. **Report.** The headset's runtime calls `ctx.report(...)` over the WebSocket: `spawned` when it appears, then the
   outcome. The bridge passes it to `engine.report()`: a hit adds 10 to the caster's score and costs the egg a crack
   or the player health; a catch adds charge and an orb; a shield is just a block.
10. **Tell everyone.** The caster's phone shows the result, the host console updates, the HUD updates, and the patch
    is removed when the spell leaves the sky. A spell with no outcome after 20 s fizzles.

## 6. Design decisions and why

These are deliberate. Keep them unless there is a reason to change them, and update this list when you do.

| Decision | Why |
|---|---|
| **Python standard library server, no framework, no build step, vendored front-end libraries** (three.js 0.170, qrcode-generator, acorn) | It runs on any laptop in a room with bad internet, and it starts in a second. Everything in the browser is plain ES modules with an import map. |
| **Structured requests (blocks + text), not free text alone** | The server can tell how much work a request needs before any LLM is involved. Pure-block requests are instant and free, which keeps the scene busy even when the LLM is slow or down. |
| **Tiers as queue priority** (preset < remix < novel) | Cheap requests should never wait behind expensive ones. The audience sees the tier live while composing. |
| **Claim / lease / ack instead of pop** | LLM calls are slow and fail. A request leaves the queue only when a forge acknowledges a valid result, and goes back to its old place on failure or timeout. Reset bumps a generation number so late answers are dropped. |
| **Two acknowledgements** (the forge built it; the headset spawned it) | A headset that reloads or drops Wi-Fi doesn't lose spells: unconfirmed ones are sent again, and the 20 s fizzle timer only starts once the spell is really in the scene. |
| **Headset is the authority for the simulation, the server for the session** | The player needs zero-latency slicing and blocking; the server needs one place for scores. Viewers follow the headset rather than simulating on their own. |
| **Scene changes as an ordered edit log of commands and patches** | Every page converges on the same scene by replaying the log, late joiners included, without the server understanding three.js. A patch with an existing name replaces the old one, so an LLM can refine its own work. |
| **Deterministic spells** (path from the headset clock and a seed) | Viewers and the host's player's view can draw the same flight without streaming positions for every spell. |
| **The harness: reuse before build** (exact → adapt → combine → new) | Speed is the main constraint in a live show. Every model-written item goes into a library, so the next similar request costs nothing. |
| **Prompts, models and settings in Markdown files that are reread when they change** | Tuning a prompt or swapping a model while the server runs, without touching Python. |
| **Model-written code is checked, guarded and budgeted** (acorn allowlist, `__k` / `__loop` guards, per-frame time and mesh limits) | Audience text reaches code that runs in the headset. The checker refuses anything outside a small API; the runtime switches off code that throws or is too slow and flies a plain shape instead. |
| **Phones on plain HTTP, the Quest on HTTPS** | The audience never sees a certificate warning; only WebXR needs a secure context. Both listeners share one game. |
| **Scene routes trust only localhost or the host key** | Patches run arbitrary JavaScript in every page, so only the laptop (and the Quest over `adb reverse`) or someone holding the key can send them. Requests forwarded from Windows into WSL arrive from a non-local address and are not trusted. |
| **SSE for game events, a WebSocket for the scene** | SSE works with the standard-library server and reconnects by itself when a phone wakes up. The scene needs two-way, high-rate messages (poses), so it gets a minimal hand-written RFC 6455 WebSocket. |
| **Sound synthesized with Web Audio, no effect files; the LLM picks a recipe per spell** | Nothing to download or license, and each spell gets a sound that matches it (`base`, `pitch`, `wobble`, `sparkle`, `length`). Only the music loop is a file. |
| **Everyone plays alone, each with a color** (no teams) | Simpler to explain at a demo; the color ties a phone to its orbs on the host screen. |
| **The player is 2× life size** in VR | The hall is huge (22 × 35 m, 17 m high) and spells come from far away; a bigger player covers more of it and reads the space better. |
| **Comfort limits** (short, small screen shake; no forced camera motion) | First-time VR users at a demo. |

## 7. Components

### `serve.py`: the server

- Serves static files (with `Last-Modified` and revalidation, so edits show on reload and big rooms are not
  re-downloaded).
- **Scene hub:** the ordered edit log (persisted to `scenes/live.json`; `scenes/master.json` is the frozen starting
  scene that `/api/reset` returns to), the `/ws` WebSocket, the headset's latest pose and scene state (`GET
  /api/state`), patches, commands, reload/reset/master.
- Mounts the **game API** (`game/http_api.py`) and the **harness** (`harness/`), and wires them together: `Harness`
  provides the `LLMForge` the engine uses, and the `CastBridge` turns casts into patches and headset reports into
  `engine.report()` calls.
- **Networking:** binds `0.0.0.0:8000`, optionally HTTPS on 8443 with a self-signed certificate in `.certs/` that is
  regenerated when the machine's addresses change (`.certs/addresses.txt`), and an optional public door on
  `127.0.0.1:<public-port>` (`PublicHandler`, which never trusts localhost). The TLS handshake runs in the
  connection's thread, so a client that stalls on the certificate doesn't block others.
- **Join address:** works out the address phones should open (`lan_ip()`; in WSL2 with NAT it asks Windows for its
  Wi-Fi address with PowerShell, checks the `netsh portproxy` table and the firewall rule, and prints the commands
  still needed). `--join-host` overrides it, `--public-url` wins over both. The address is served at
  `/api/join-info` and `/api/links`.
- **Quest helpers:** `keep_quest_forwarded()` keeps `adb reverse tcp:8000` alive over USB or Wi-Fi;
  `watch_headset()` reports the headset's connection; Quest performance stats come from `adb` when reachable.
- Logging is quiet by default (`QuietServer`, `log_request`): tokens and keys are scrubbed from URLs.

### `game/`: the game server

| File | Responsibility |
|---|---|
| `catalog.py` | Loads and checks `shared/catalog.json`: shapes, assets, motions, preset spells, targeting mode, player health settings. |
| `spell_request.py` | `Recipe` (the parsed parts), tiers, `Status`, `resolve_preset()` (builds a spell from blocks with a derived name and incantation), `validate_spell()` (the contract every forged spell must pass). |
| `request_queue.py` | Priority queue ordered by (tier, arrival) with `claim` / `ack` / `nack`, leases, attempts and a generation number. |
| `forge.py` | The forge interface (`submit(job)`, `capacity`, `online`), `ForgeJob` (brief, lease, ack/nack), `CatalogForge`, `NotConnectedForge`. |
| `engine.py` | Members (color from `member_color()`, score), submit and cooldown, the round (acts, clock, cracks, charge, player health and healing), targeting, pacing, outcome scoring, `state()` and `host_view()`, publishing. All game constants are at the top. |
| `events.py` | Fan-out to SSE subscribers by role (and by member for audience events). |
| `http_api.py` | The `/api/` game routes and the SSE streams. |

### `harness/`: the LLM scene agent

Full description in [`harness/README.md`](harness/README.md). In short:

- `forge.py` (`LLMForge`): the reuse ladder for spells, prompt assembly, answer checks, clamping, the fallback preset.
- `scene.py`: the scene-edit queue (`POST /api/scene/requests`, `POST /api/chat`) with priorities from tags.
- `pool.py`, `backends.py`: parallel slots, the `claude-cli` and `api` backends, pausing at usage limits.
- `library.py`: the library in `assets/` (prebuilt, committed) and `assets/generated/` (model-written, not
  committed): search, candidate → verified → quarantined.
- `bridge.py`: the cast bridge (spells → patches, headset reports → engine).
- `codecheck/`: the acorn-based static check (`check.mjs`, `rules.json`).
- `runtime/spell-runtime.js`: the patch that flies every spell; `runtime/spell-api.md`: what model code may use.
- `workers/*.md` (one per job: `spell-adapt`, `spell-combine`, `spell-new`, `scene-editor`, plus `_policy.md` shared
  by all), `tools/*.md` (answer schemas), `tags.md`, `orchestrator.md` (settings).

A forged spell can carry, beyond the `validate_spell` fields: `target` (`egg` or `user`), `behaviors` (library
behaviors with parameters), a library `object`, `flavor` text, a `sound` recipe, and `harness` metadata (step, model,
timing). The engine passes unknown fields through to the headset unchanged.

### `index.html`: the scene

One page plays two roles. In the Quest's browser (or with `?headset` / `?emulate`) it is **the headset**: the one
interactive copy. Everywhere else it is a **viewer**.

| URL parameter | Effect |
|---|---|
| `?headset` | This desktop tab is the headset (mouse flick slices, sounds play, HUD shows). |
| `?emulate` | Like `?headset`, with IWER's emulated Quest 3 and its DevUI (loaded from esm.sh). |
| `?mirror` | A viewer that starts in the headset's own view (V toggles). |
| `?clean` | No status text over the view (used by `/control/`). |
| `?scene=path.glb` | Another room. Default `castle-hall/castle-hall.glb`; `library/library.glb` also works. |
| `?hatch=chest` | What a surviving egg hatches into (default `dragon`, `assets/models/emerald-dragon.glb`). |
| `?mute` | No sound on the headset. |
| `#key=<host key>` | Sends the host key with the WebSocket, for the Quest over Wi-Fi. |

What the page contains, in source order:

- **Room loading.** A glTF/GLB (meshopt-compressed; the `.glb` rooms are their `.gltf` originals optimized, see the
  comment above `SCENE_URL` for the command), scaled to meters (`SCENE_SCALES`), centered with the floor at y=0, the
  player placed at `SCENE_SPAWNS`. The **egg** is every mesh whose name starts with `Egg` (or a stand-in if the room
  has none); `ctx.egg()` returns it.
- **Editable objects.** Named groups described by a small spec (`name, shape, color, size, position, animation,
  glow`), changed by `add_object` / `update_object` / `remove_object` commands from the edit log. `sceneState()` is
  what the scene-editor worker sees.
- **Sound** (headset only). Web Audio recipes (`whoosh`, `shatter`, `thud`, `crack`, `block`, `swish`, `eggBreak`),
  per-spell recipes, positional (HRTF) when given a position; a CC0 music loop starts on the first click or Enter VR.
- **HUD** (headset only). A canvas panel below the line of sight: egg shells left, the player's health bar with
  lost health draining behind it, the clock and act (red in the last 30 s). Fed by polling `/api/game/state` every
  0.4 s.
- **Hit feedback.** A short shake, a red flash and "Ouch!" on a hit (`ctx.hit()` from the runtime, or noticed in the
  game state).
- **End of round.** `broken`: the egg shatters into shards with physics, then the view fades to dark with a sad face.
  `hatched`: the egg splits, the dragon (or a chest) rises and spills gems or coins. A reset restores everything.
- **Player body.** Gravity, `STEP` for walking onto low things, jumping (A / Space), climbing (hold left grip / C by
  a ledge), sprint (left trigger / Shift). Colliders are pillars built from the hall's centerpiece meshes.
- **Controls.** VR: left stick moves, right stick snap-turns, left hand holds the shield, right hand the blade.
  Desktop: drag to look, WASD/arrows.
- **Live sync.** The WebSocket, applying ops in order once the room has loaded, streaming pose and scene state from
  the headset, and errors back to the server.
- **Patches.** `setPatch(id, code)` runs code as an async function with `THREE` and `ctx`. The `ctx` API (`root`,
  `onFrame`, `on`, `onRemove`, `load`, `import`, `head`, `sound`, `egg`, `isViewer`, `hit`, `report`, and more) is
  documented in the comment above `setPatch()`. Read it before writing a patch.
- **Viewer.** An avatar for the headset's head and hands, easing to its pose; orbit or headset view.

From the browser console: `sceneState()` shows what the scene editor is sent, `applyCommands([...])` applies edits
locally.

### `audience/index.html`: the phone app

- **Join**: enter a name, then a short "The mirror sees…" reveal of the name in the member's color. The token is
  kept in `localStorage`; a server restart sends phones back to the join screen.
- **Status card**: name badge, clock (or "Not started" / "Over"), egg cracks, and "⭐ score #rank". A banner shows
  "⏳ Starting soon", "🐣 It hatched!" or "💥 The egg broke!".
- **Two tabs** (the choice is remembered in `localStorage` as `midnight-egg-tab`):
  - **Prebuilt**: a two-column grid of big preset-spell tiles; one tap casts. Tiles show "⏳ Ns" during cooldown.
  - **Custom**: "Pick a thing + a move, or type an idea." A composer that holds block chips and free text, a live
    tier hint (⚡ Instant / ✨ Remix / 🔮 Brand new, "· waits" while the forge is offline), and the Things and Moves
    palettes.
- **Sent list**: each request's parts as emoji, its spell name, and its status as emoji.
- **Buzz**: when a spell is returned at the member, the phone vibrates and flashes 🪃.

The UI aims for few words and big targets: it is used standing up, in a dark room, in a hurry.

### `host/index.html`: the host console

Needs the host key. Shows the queue as a **cauldron line** of orbs (one per request: emoji, caster color, tier badge,
a countdown ring while a forge works on it), Ready and In the sky lists, the roster (color, score, casts, hits,
online dot), the top three scorers, Start / Reset round, and the **player's view**: a 2D tower-at-night stand-in for
the headset, with casters on broomsticks, spells channeling and flying their motion, and outcomes animated. It has a
Full screen button (which also hides the address bar with its key). It updates when the server pushes `host:view`,
not on a timer.

### `join/index.html` and `control/index.html`

- `/join/`: a QR code for the phones' address (`/api/join-info`) and a live joined count. Safe to project.
- `/control/`: the scene (`/?clean`) and the host console side by side, for running the show from the laptop. It
  contains the host key, so don't project it.

### Data and assets

| Path | What |
|---|---|
| `shared/catalog.json` | The building blocks: shapes, 6 assets ("things"), 6 motions ("moves"), 5 preset spells, `targeting`, `player` health. Each block has an emoji used by the phone UI. |
| `assets/index.json`, `assets/objects/`, `assets/behaviors/` | The prebuilt library offered to the LLM (code files are function bodies; see `harness/runtime/spell-api.md`). |
| `assets/generated/` | What models wrote. Not committed; review an item, then promote it into the prebuilt list. |
| `assets/models/` | glTF models: `emerald-dragon.glb` (the hatch), `RobotExpressive.glb` (patch demo). Credits in `CREDITS.md`. |
| `assets/audio/` | The music loop (CC0). Credits in `CREDITS.md`. |
| `castle-hall/` | The default room (original geometry; `castle-hall.glb` is the compressed one the page loads). |
| `library/` | The earlier test room ("library" by Pasha, CC-BY-4.0, `library/license.txt`), still loadable with `?scene=library/library.glb`. |
| `potions-room/` | An alternative room built for the project, not wired into the page. |
| `patches/` | Example patches (`ball-slice.js`, the origin of the spell runtime; `walking-robot.js`; `angry-eye.js`). Send with `curl -X POST --data-binary @patches/x.js 'localhost:8000/api/patch?id=x'`. |
| `scenes/master.json` | The starting scene (empty). `scenes/live.json` is the current log (not committed). |
| `scripts/quest-wireless.sh` | Switch the Quest to wireless adb once, with the cable plugged in. |
| `scene_agent.py` | The original single-call scene editor. Unused: its prompt and tools now live in `harness/workers/scene-editor.md` and `harness/tools/scene-commands.md`. |

## 8. Game rules as implemented

| Rule | Value | Where to change it |
|---|---|---|
| Round | 3 acts: dusk 60 s, storm 90 s, midnight 30 s | `ACTS` in `game/engine.py` |
| Spells in the sky at once | dusk 2, storm 5, midnight 3 | `ACTS` |
| Egg | breaks at 5 cracks (one per hit on the egg); the round ends `broken` | `MAX_CRACKS` |
| Win | the egg survives to midnight; the round ends `hatched` | `tick()` |
| Player health | 100; a hit on the player costs 10 × the spell's power; heals 1 per second during a round. Reaching 0 has no effect yet. | `player` in `shared/catalog.json` |
| What spells fly at | `harness`: the forge picks per spell (heavy or destructive → egg, quick or sneaky → player), others random 50/50; or `mix`, `egg`, `user` | `targeting` in the catalog, or `--targeting` |
| Scoring | +10 to the caster for a hit; −5 to the target of a return | `HIT_POINTS`, `RETURN_PENALTY` |
| Catch | adds the spell's power to charge and keeps it as an orb (for a future return) | `report()` |
| Cooldown | 5 s per member, and at most one spell queued, forging or ready per member | `COOLDOWN_S` |
| Fizzle | a spell in the scene with no outcome for 20 s | `SPELL_LIFETIME_S` |
| Leases | preset 5 s, remix 20 s, novel 60 s; 2 attempts | `LEASE_SECONDS`, `MAX_ATTEMPTS` |
| Channel time | presets 0.5 s, others 2.5 s (the spell grows in place before it flies) | `preset_channel_ms`, `channel_ms` in the catalog |
| Outcomes on the headset | sliced by the right-hand blade (or a mouse flick) = catch; touched the left-hand shield = shield (clang); reached the egg's box or the player's capsule = hit; flew past = shield | `harness/runtime/spell-runtime.js` |
| Spawn position | egg spells appear between the player and the egg (±60°); player spells appear in front of the player | `launch()` in the runtime |

Members keep their color across rounds; scores reset with each new round.

## 9. Spell requests and the queue

*Built: `game/spell_request.py`, `game/request_queue.py`, `audience/index.html`.*

A request is the ordered list of pieces the audience member composed:

```json
{ "parts": [
  { "type": "ingredient", "kind": "asset", "id": "fireball" },
  { "type": "text", "text": "that splits into three" }
] }
```

The server validates every piece against the catalog (at most 8 pieces and 120 characters of text) and sorts the
request into a **tier** by how much LLM work it needs. The tier is also its queue priority, and the phone shows it
live while composing:

| Tier | Rule | What happens | Priority |
|---|---|---|---|
| ⚡ **Preset** | Only catalog blocks and no text: at most one spell, one asset, one motion (an asset alone uses the default motion; an asset or motion block overrides that half of a preset spell) | Resolved from the catalog with a derived name and incantation, e.g. *Zigzagging Fireball*, "Flammizzo". **Never touches the LLM.** | First |
| ✨ **Remix** | At least one existing spell or asset, plus text or several blocks to combine | The LLM makes a small, local edit (new motion, a recolor, a behavior, a combination) | Second |
| 🔮 **Novel** | Text with no spell or asset to start from (a motion block is allowed as a hint) | The LLM builds the whole spell, from library items or new code | Last |

The engine always takes presets and takes remix and novel requests only when the LLM forge has free capacity. A
request leaves the queue only when its forge acknowledges it. Resolved spells wait in the **ready** stage until the
act has room; spells cast before the round starts wait there for dusk.

Request statuses: `queued` (with how many are ahead) → `forging` (claimed, still in the queue) → `ready` → `sent` (to
the headset) → `cast` (the headset confirmed it) → `hit` / `caught` / `shielded` / `fizzled`, or `failed` /
`expired`.

## 10. Acknowledgements and the forge contract

*Built. The interface lives in [`game/forge.py`](game/forge.py) (docstring), [`game/spell_request.py`](game/spell_request.py)
(`validate_spell`) and [`game/engine.py`](game/engine.py) (`LEASE_SECONDS`, `MAX_ATTEMPTS`). The harness's `LLMForge`
implements it; this section is what you need to write another forge.*

A request is only removed when the next stage **acknowledges** it. There are two hand-offs, each with its own ack:

```
QUEUE                                       FORGE
queued ──claim (lease)──▶ forging ────────▶ PRESET:       CatalogForge, acks at once
   ▲                         │              REMIX / NOVEL: the LLM forge, acks later
   │                         │                    │
   └── nack / lease timeout ─┘                    │ job.ack(spell) → validated → removed from queue
       (same place; fails after 2 tries)          ▼
                                                ready ──(paced by the act)──▶ sent ──patch "spell-<id>"──▶ HEADSET
                                                                                │
                                                       ctx.report → bridge → engine.report("spawned")
                                                                                ▼
                                                                    cast (in the scene) ──▶ hit / caught / shielded / fizzled
```

### Hand-off 1: forge → "the spell is built"

- The engine **claims** the first request it can serve. The request keeps its place with status `forging` and is
  handed to a forge as a `ForgeJob` carrying a **lease** (request id, attempt number, queue generation, deadline).
- Presets and LLM requests use the same contract. `CatalogForge` calls `job.ack()` before returning, so presets go
  through claim → ack → remove in microseconds.
- `job.ack(spell)` removes the request from the queue, checks the spell with `validate_spell`, and moves it to ready.
  A spell that fails validation counts as a nack.
- `job.nack(error)`, or a lease that runs out, puts the request back **at its original position**. After
  `MAX_ATTEMPTS` (2) claims it is marked `failed`.
- **Reset** clears the queue, including claimed requests, and bumps the generation. Outstanding leases become invalid,
  so an LLM call that finishes after the reset gets `False` from `ack()` and its result is dropped. The same happens
  to an answer that arrives after its lease ran out.
- The engine never has more remix/novel jobs claimed than the forge's `capacity`.

### Hand-off 2: headset → "the spell is in the scene"

- When a ready spell gets a slot in the sky, the engine marks it `sent`, publishes `spell:cast` (SSE roles
  `headset`, `spectator`), and the cast bridge turns it into a patch.
- The headset's spell runtime reports `spawned` through `ctx.report`; the spell becomes `cast`, and only then does
  its 20 s fizzle timer start. Repeats are harmless, and an outcome report also counts as confirmation.
- Sent but unconfirmed spells are sent again when a headset connects. They keep their slot until confirmed.
- For testing without a headset, report events with `curl` to `POST /api/headset/event` and the host key.

### Writing a forge

```python
class MyForge:
    capacity = 2                 # max jobs at once; the engine never hands you more
    online = True                # optional; False makes the apps say "the forge is offline"

    def submit(self, job):       # must return quickly: do the work on another thread
        threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def _run(self, job):
        try:
            brief = job.brief()                      # the request with every block expanded (below)
            spell = call_the_llm(brief, timeout=job.seconds_left())
            if not job.is_current():                 # reset or timed out meanwhile: drop it
                return
            job.ack(spell)                           # False: stale, or failed validation (logged)
        except Exception as err:
            job.nack(str(err))                       # always answer, or the lease has to run out
```

`serve.py` wires the forge in: `GameEngine(catalog, events, llm_forge=harness.forge)`.

**Input:** `job.brief()` returns the request with each block expanded to its catalog entry:

```json
{ "request_id": "s7", "tier": "remix", "attempt": 1,
  "text": "that splits into three",
  "parts": [
    { "type": "ingredient", "kind": "asset", "id": "fireball",
      "block": { "label": "Fireball", "shape": "sphere", "color": "#ff6a1f", "size": 0.3, "glow": true, "...": "..." } },
    { "type": "text", "text": "that splits into three" } ] }
```

`job.catalog` has the rest of the menu; `job.request` is the full `SpellRequest` (read only).

**Output:** `job.ack(spell)` with a dict that passes `validate_spell`:

| Field | Required | Rule |
|---|---|---|
| `name` | yes | non-empty string, ≤ 40 characters |
| `incantation` | yes | one word, 2–20 letters (apostrophe and hyphen allowed) |
| `asset.shape` | yes | one of `catalog.shapes` (`box`, `sphere`, `cylinder`, `cone`, `torus`, `book`); the fallback when library code fails |
| `asset.color` | yes | `#rrggbb` |
| `asset.size`, `asset.glow` | no | meters, clamped to 0.05–1.5 (default 0.3); bool |
| `motion.id` | yes | a catalog motion (`straight`, `homing`, `zigzag`, `spiral`, `arc`, `boomerang`) |
| `motion.speed` | no | clamped to 0.5–5 |
| `power` | no | integer 0–5, default 1 |
| `channel_ms` | no | 500–6000, default 2500 |
| `target` | no | `egg` or `user`; used when targeting is `harness` |
| anything else | no | passed through to the headset unchanged (`behaviors`, `sound`, `flavor`, library `object`, …) |

**Rules:** answer every job exactly once (`ack` or `nack`, from any thread); `ack`/`nack` return `False` when the job
is no longer current or the spell failed validation (the reason is logged by `game.engine`); never retry an `ack` on
the same job; finish within `job.seconds_left()`; don't touch the engine, queue or request objects.

**Testing:** the `FakeForge` in `tests/test_game.py` collects jobs and the tests answer them by hand.

## 11. Network protocol and API

Clients send small JSON requests and receive pushed events over **Server-Sent Events** (one stream per client). The
scene uses one **WebSocket** per page. Errors come back as `{ error }` with a 4xx status. All other paths are static
files.

### Scene routes

Only answered for requests from this machine or carrying the host key (`X-Host-Key`, or `?key=` on the WebSocket).

| Request | Body → response |
|---|---|
| `GET /ws?role=headset\|viewer` | WebSocket: `init` (the whole edit log), then each new `op`. The headset sends `pose` (30/s), its scene state (10/s), `reply` (to snapshot requests), game reports (`ctx.report`), and `error`. |
| `GET /api/state` | → latest pose, the headset's scene state, patch names, recent edits and page errors, Quest performance (`?scene=0` skips the scene) |
| `POST /api/commands` | `{ commands: [{ name, input }] }` → `{ seq, applied? }` |
| `POST /api/patch?id=NAME`, `DELETE /api/patch?id=NAME` | JavaScript body → whether it ran on the headset; `DELETE` removes it |
| `POST /api/reload` | reload every page (the log is replayed) |
| `POST /api/reset` | replace the live scene with the master, and reload every page |
| `POST /api/master` | make the live scene the new master |
| `POST /api/chat` | `{ message, scene }` → `{ commands, text, model, ms }`: a scene edit through the harness's scene queue. The page no longer has a chat box; use `curl` or another client. |
| `POST /api/scene/requests` | `{ text, tags?, wait? }` → the queued scene job (`{ id, status, result? }`); tags are in `harness/tags.md` |
| `GET /api/harness` | → slots, running jobs, the scene queue, recent jobs (step, model, time), reuse rate, library counts |
| `GET /api/links` | → the audience URL (no key needed) |

### Game routes

| Request | Auth | Body → response |
|---|---|---|
| `GET /api/catalog` | — | → `shared/catalog.json` |
| `GET /api/join-info` | — | → `{ audience_url }` |
| `GET /api/game/state` | — | → `phase`, `outcome`, `round_seconds`, `remaining_s`, `cracks`, `max_cracks`, `player` (`hp`, `max_hp`), `targeting`, `charge`, `scores` (member id → score), `leaderboard` (top 5: `id`, `name`, `color`, `score`), `queue` (counts per tier), `forging`, `ready`, `in_flight` (spells), `orbs`, `audience`, `forge_online`, `cooldown_s` |
| `POST /api/join` | — | `{ name }` → `{ token, member, requests, cooldown_s }` |
| `GET /api/me` | `X-Token` | → `{ member, requests, cooldown_s }` |
| `POST /api/spells` | `X-Token` | `{ parts }` → `{ request, member, requests, cooldown_s }`; 429 with `retry_after` while cooling down |
| `GET /api/events?role=…` | `token` query param for `audience`, `key` for `host` | SSE stream for `audience`, `headset`, `spectator` or `host` |
| `POST /api/headset/event` | `X-Host-Key` | `{ type: "spawned" \| "hit" \| "catch" \| "shield" \| "return", spell_id, target_member_id? }` → game state |
| `POST /api/admin/start`, `/api/admin/reset` | `X-Host-Key` | → game state |
| `GET /api/admin/view` | `X-Host-Key` | → the same as `host:view` |

### SSE events

| Event | To | Payload |
|---|---|---|
| `game:state` | all | game state, every second and on every change |
| `me` | that audience member, on connect | `{ member, requests, cooldown_s }` |
| `spell:status` | the caster | `{ id, tier, status, parts, ahead?, spell?: { name, incantation }, error? }` |
| `spell:cast` | `headset`, `spectator` roles | the full spell: `id`, `name`, `incantation`, `power`, `channel_ms`, `asset`, `motion`, `caster`, `target`, plus forge extras. Sent again on headset connect until confirmed. (The headset page doesn't listen to it: it receives spells as patches from the cast bridge, which also re-patches unconfirmed casts. The SSE copy is for other clients.) |
| `audience:buzz` | the member a spell was returned at | `{ spell, incantation, penalty }` |
| `game:over` | all | `{ outcome: "hatched" \| "broken", charge, caught, scores, winners }` (`winners`: member ids with the top score, if above 0) |
| `host:view` | host | `roster` (name, color, online, score, casts, hits, latest request), `queue` (serving order, with `ahead`; claimed ones have `status: "forging"` and `lease: { attempt, seconds_left, seconds }`), `ready` and `in_flight` (with the full `spell`), each with its `caster`; `recent`: `{ id, status }` of the last 20 spells to leave the sky |

## 12. Running the demo in one room

Everything runs on **one laptop**; phones, the Quest and the projector connect to it over the room's Wi-Fi.

```
                 room Wi-Fi (one network)
   phones ──────────▶ http://<laptop>:8000/audience/     audience app
   projector ───────▶ http://localhost:8000/join/       QR code + joined count (on the laptop)
   host ────────────▶ http://localhost:8000/host/#key=… roster, queue, start / reset (on the laptop)
   Quest ───────────▶ https://<laptop>:8443/#key=…      the scene (self-signed certificate)
```

Phones use plain HTTP, so nobody in the audience sees a certificate warning. Only the Quest needs HTTPS, for WebXR.
Both listeners run in the same process and share one game. The scene, the apps and every library are local; only
Claude calls and `?emulate` need internet.

### 1. The network

- **Use a network you control:** a travel router, or a phone hotspot for small groups. Venue and guest Wi-Fi often
  turns on *client isolation*, which stops phones from reaching the laptop even though both are "on the Wi-Fi". This
  is the most common failure. HTTPS cannot fix a blocked network path.
- Phone hotspots usually cap connected devices at about 5–10; for a bigger audience use a router.
- The laptop, the Quest and every phone must be on the same network.

### 2. The laptop

**macOS:** the first time, allow Python to accept incoming connections when macOS asks (or in System Settings →
Network → Firewall). Keep the laptop awake, e.g. `caffeinate -d` in another terminal.

**Windows with WSL2:** by default WSL2 is behind NAT, so phones can reach Windows but not WSL. `serve.py` detects
this, asks Windows for its Wi-Fi address (a few seconds at startup), and prints any commands still needed. Pick one:

- *Mirrored networking (recommended; Windows 11 22H2 or newer).* WSL then shares Windows' address. In
  `%UserProfile%\.wslconfig`:
  ```ini
  [wsl2]
  networkingMode=mirrored
  ```
  Then run `wsl --shutdown` and, once, in an administrator PowerShell:
  ```powershell
  New-NetFirewallHyperVRule -Name WorldTinder -DisplayName WorldTinder -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8000,8443
  ```
- *Port forwarding (NAT mode).* Run the `netsh interface portproxy …` and `New-NetFirewallRule …` commands that
  `serve.py` prints in an administrator PowerShell. WSL gets a new address when it restarts, so `serve.py` checks the
  forwarding on every start and prints the commands again if it's out of date.

Keep Windows from sleeping as well. Network setup is per machine: the detected address and the certificate (in
`.certs/`, not committed) are worked out on each laptop at startup, but the one-time firewall and forwarding steps
above must be done on every new machine.

**Wrong address in the QR code?** With several networks (Wi-Fi plus Ethernet or VPN), pass the right one:
`python serve.py --https --join-host 192.168.1.23` (or `WORLDTINDER_JOIN_HOST`).

**Devices can't reach each other at all?** Go through a tunnel: `python serve.py --public-port 8080 --public-url
https://<this machine>.<tailnet>.ts.net:8443` and `tailscale funnel --bg --https=8443 http://127.0.0.1:8080`. Phones
then get the tunnel's address in the QR code. Never point a tunnel at port 8000: its requests come from this machine,
which port 8000 trusts with the scene routes; the public door doesn't.

### 3. The Quest

- **Over Wi-Fi:** open the printed `https://<lan-ip>:8443/#key=…` and accept the certificate warning.
- **Over USB or wireless adb (no key, no certificate):** connect by USB once and run `scripts/quest-wireless.sh`, then
  unplug and open **`http://localhost:8000/`** (include `http://`; this port does not speak HTTPS). The server keeps
  `adb reverse` working when USB or Wi-Fi drops, preferring USB while attached. Run the script again after the
  headset reboots.

### 4. Start and check

```bash
WORLDTINDER_HOST_KEY=<pick one> python serve.py --https
```

1. Open the **join screen** (`http://localhost:8000/join/`) full screen on the projector.
2. Scan the QR code with your own phone **with mobile data off**, and join. If it doesn't load, it's the network or
   firewall.
3. Open the **host console**; your phone appears in the roster with a green dot.
4. Cast a prebuilt spell; it appears in the host console.
5. Open the scene on the Quest, enter VR, press **Start round** on the host console.

Notes:
- The game is in memory: restarting `serve.py` forgets everyone, and phones go back to the join screen. The scene's
  edit log survives restarts (`scenes/live.json`).
- Desktop browsers allow only about 6 open connections per server over HTTP/1.1, so more than about 5 audience tabs
  in one browser will stall. Use several browsers or phones.
- Don't put the host console or `/control/` on the projector: their address contains the host key.

## 13. Project structure

```
WorldTinder/
├── index.html              # the WebXR scene: headset or viewer (three.js, no build step)
├── serve.py                # static server, scene hub (edit log, /ws, patches), mounts game/ and harness/, LAN/HTTPS/WSL setup
├── game/                   # the game server
│   ├── catalog.py          #   loads and checks shared/catalog.json
│   ├── spell_request.py    #   requests, tiers, statuses, preset resolution, validate_spell
│   ├── request_queue.py    #   priority queue with claim / ack / nack and leases
│   ├── forge.py            #   forge interface, ForgeJob, CatalogForge
│   ├── engine.py           #   members, round, pacing, targeting, scoring, state
│   ├── events.py           #   SSE fan-out
│   └── http_api.py         #   /api/ game routes
├── harness/                # the LLM scene agent (see harness/README.md)
│   ├── forge.py, scene.py, pool.py, backends.py, library.py, bridge.py, loader.py
│   ├── orchestrator.md, tags.md, workers/*.md, tools/*.md   # settings and prompts, reread on change
│   ├── runtime/            #   spell-runtime.js (flies every spell), spell-api.md (what model code may use)
│   └── codecheck/          #   acorn-based static check for model code
├── audience/index.html     # phone app
├── host/index.html         # host console: cauldron line, roster, scores, start / reset, player's view
├── join/index.html         # projector join screen: QR code, joined count
├── control/index.html      # scene + host console side by side
├── shared/catalog.json     # building blocks: assets, motions, preset spells, targeting, player health
├── assets/                 # library (index.json, objects/, behaviors/), models/, audio/; generated/ not committed
├── castle-hall/            # the default room
├── library/                # the earlier test room (CC-BY-4.0)
├── potions-room/           # an alternative room, not wired in
├── patches/                # example patches for POST /api/patch
├── scenes/master.json      # the starting scene; live.json (not committed) is the current edit log
├── scripts/                # quest-wireless.sh, wifi-5ghz.swift
├── tests/                  # test_game.py, test_harness.py, test_quest_connection.py
├── vendor/                 # three.js 0.170, qrcode-generator 1.4.4, acorn
├── scene_agent.py          # unused original scene editor
├── environment.yml         # conda env: Python 3.12, anthropic
└── .claude/launch.json     # preview config that runs serve.py
```

## 14. Working on this repo

**Before changing things**

- Read the docstrings at the top of `serve.py`, `game/engine.py`, `game/forge.py`, `game/spell_request.py` and
  `harness/bridge.py`, and the comment above `setPatch()` in `index.html`. They are kept up to date and explain the
  contracts.
- `python -m unittest discover tests` must pass (65 tests, about 2 s). Add tests for game logic and harness changes;
  `tests/test_game.py` has a `FakeForge` and a fake clock, `tests/test_harness.py` a fake backend.

**Style**

- Match the surrounding code: plain Python standard library, plain browser JavaScript, no frameworks, no build.
  Comments explain *why* in full sentences; keep their density similar to what's there.
- Front-end libraries are vendored, never loaded from a CDN (the room may have no internet).
- New tunable numbers go at the top of the file as named constants, or into `shared/catalog.json` if they're game
  balance.
- Prompts and model choices belong in `harness/*.md`, not in Python.
- Each page is one self-contained HTML file with inline CSS and JS. The pages share a dark purple palette
  (`--bg: #14122b`) and use emoji as icons.

**Where to change what**

| You want to | Change |
|---|---|
| Add a block (thing, move, preset spell) | `shared/catalog.json` (the phone UI reads it; assets need a shape from `shapes`) |
| Add a motion the runtime can fly | `harness/runtime/spell-runtime.js`, then the catalog, then `harness/tools/forge_spell*.md` |
| Add a library object or behavior | `assets/objects/` or `assets/behaviors/` + `assets/index.json` (a test checks it passes codecheck) |
| Tune game balance | constants in `game/engine.py`, `player` and `targeting` in the catalog |
| Change what the LLM does | `harness/workers/*.md`, `harness/workers/_policy.md`, `harness/tags.md` |
| Change a sound | the recipes in the Sound section of `index.html` (and `SOUND_BASES` in `harness/forge.py` for spell bases) |
| Add a new kind of scene change | a patch (see `patches/`), or a new command in `index.html` + `harness/tools/scene-commands.md` |
| Add an API route | `game/http_api.py` (game), or `serve.py` (scene; add POST routes to `SCENE_POST_ROUTES` so they are key-protected) |

**Testing in a browser**

- One tab with `?headset` plus one or more phones (or narrow desktop windows on `/audience/`) is enough to play a
  full round on a laptop. Press Start round on the host console.
- `curl -s localhost:8000/api/game/state`, `/api/harness` and `/api/state` show what the server thinks.
- To fake headset outcomes: `curl -X POST -H "X-Host-Key: $KEY" -d '{"type":"hit","spell_id":"s3"}' localhost:8000/api/headset/event`.
- Headless Chromium screenshots of `/audience/` at 390 px wide are a good check of the phone layout.

**Gotchas**

- Don't kill the server with `pkill -f serve.py` from a script whose own command line contains that text; it kills
  the script. Stop it by PID or with Ctrl-C.
- A port already in use makes `serve.py` exit at once with a clear message (another server is probably running).
- The phone's `localhost` is the phone. Phones always need the LAN address.
- Patches also run on viewers: guard side effects with `ctx.isViewer`.
- Model output is untrusted twice over: the server clamps it (`harness/forge.py`, `validate_spell`), and the page
  clamps it again (`cleanSpec`, the sound recipe clamps, the runtime budgets). Keep both.
- `assets/generated/`, `scenes/live.json`, `.certs/` and `tmp/` are not committed.

## 15. Known gaps and next steps

Roughly in order of value for the demo:

1. **Return spells.** Caught spells are kept as `orbs` and the server supports `return`, but nothing in the headset
   triggers it. Planned: shout the incantation (speech-to-text against the small live vocabulary) and flick toward a
   caster.
2. **Casters in the scene.** Broomstick avatars around the hall in each member's color, with spells launching from
   them (today spells appear near the egg or in front of the player).
3. **Player health at 0.** Nothing happens yet. Decide whether it ends the round, stuns the player, or costs a crack.
4. **The hatch from the round's spells.** Today the egg always hatches the same dragon. Planned: the LLM designs a
   creature from the caught spells, and `game:over` carries it.
5. **Charge.** Tracked but no longer shown; either use it for the hatch or remove it.
6. **Voice and audio** with ElevenLabs (chants, narrator, speech-to-text).
7. **End screen on phones** with the hatch and awards; "Most Magical Spell" award chosen by the LLM.
8. **Safety gaps:** no rate limit on scene requests, no kill switch that pauses the queue, no name filter, no
   moderation step for text shown on screens (the worker policy turns hateful requests into a "Fizzle" spell).
9. **Comfort:** the game still has stick locomotion, jumping and climbing for exploring the hall. Consider locking
   the player near the egg for the game itself.

---

# Part II: the design target

Everything below is the game as designed. Notes in *italics* say how the built system differs.

## 16. Core idea

| Element | What it is |
|---|---|
| **Player** | One person in a Quest headset, with a wand (right controller or hand) and a shield hand (left). |
| **Audience** | Anyone nearby with a phone. They join by QR code, each playing for themselves, and send spells. |
| **LLM** | The game's "spell forge": it turns audience requests into code that runs in the scene, merges spells for the finale, and designs the hatched creature. |
| **Scene** | The single source of truth for everything in the world. All rendering, gameplay and LLM edits go through it. |
| **ElevenLabs** | Voices for spell incantations, the narrator and the creature; generated sound effects; speech-to-text for the player's spell words. |
| **Spectator screen** | A projector showing a third-person view, the spell feed, the clock and the top scorers. *Today: the host console's player's view.* |

**Why VR:** spells come from all 360°, including behind you, at different depths. You hear each caster chant from
their direction before the spell fires, so you turn, track and react with your whole body.

## 17. Game design

### The goal

- An **egg rests in a cradle** near the player. Spells are aimed at it, or at the player.
- A **clock** counts down to midnight. One round lasts about **3 minutes**.
- **Win:** the egg is intact at midnight. It hatches into a creature designed from the spells you caught.
- **Lose:** the egg takes too many hits (**5 cracks**) and breaks open early: a funny failure, not a punishing one.
- **Charge:** each caught spell charges the egg. More charge, and more *different* spells caught, produce a bigger,
  more elaborate creature.

### The player's three actions

| Action | Input | Effect | Trade-off |
|---|---|---|---|
| **Shield** | Raise the left hand (*planned: also shout "Aegis!"*) | A glowing barrier blocks spells | Safe, but earns no charge |
| **Catch** | Slice a spell in flight with the wand/blade | The spell becomes a glowing orb that charges the egg | Needs aim and timing; a miss means a hit |
| **Return** | Shout a caught spell's incantation and flick toward a caster | Fires the spell back at the caster | Uses up the orb; the caster loses points and their phone buzzes. *Not built.* |

The central decision is **shield safely vs. catch for progress**.

### Spells have names

When an audience member writes *"a teapot that screams and chases them,"* the LLM produces a **name** (*Shrieking
Kettle*), an **incantation** (*"Kettlenox!"*), and the code that makes it look, fly and sound as described. If the
player catches it, *"Kettlenox!"* becomes a word they can shout to send it back, so the player's vocabulary grows
during the round, and every word in it comes from the crowd. Incantations should be one invented, pronounceable word
that doesn't sound like the shield word or any spell in play.

### The round in three acts

| Act | Time | What happens |
|---|---|---|
| **Dusk** | 0:00–1:00 | A few spells at a time, mostly from in front. The player learns to shield and catch. |
| **Storm** | 1:00–2:30 | Volleys from every direction, weather effects, audience-summoned creatures (stretch). |
| **Midnight** | final 30 s | The audience's last spells are **merged by the LLM into one giant spell**: huge, slow, caught with a two-handed motion. Success gives a "legendary" hatch; failure costs 2 cracks. *Not built: midnight is a normal act.* |

### The hatch

At midnight the LLM receives the caught spells and designs a creature from them (fire + paper birds + kettle → a
flame-winged teapot dragon), assembled from a parts library (body, head, wings, tail, accessories, colors, particles),
with a name, a narrator line, and credits: *"Hatched from spells by Maya, Theo and Priya."* *Today: a fixed dragon
model rises and spills gems.*

### Scoring

- Everyone plays for themselves, each with their own color.
- +10 when your spell hits; −5 when the player returns a spell at you.
- Awards at the end: the top scorer, plus a **"Most Magical Spell"** chosen by the narrator (LLM), which rewards
  creativity over spamming fireballs. *Not built.*

## 18. Audience experience (target)

1. **Join:** scan the QR code, enter a name.
2. **Welcome:** the enchanted mirror reveals your name in your color; your broomstick appears around the tower.
   *Built, except the broomstick.*
3. **Cast:** prebuilt spells in one tap, or compose a custom one. *Built.*
4. **Brewing → Channeling → Result:** live status (Hit!, Caught!, Shielded) with the spell's name. *Built.*
5. **Cooldown:** 5 s per person. *Built.*
6. **Getting hit:** if a spell is returned at you, your phone buzzes and flashes. *Built on the phone; nothing sends
   returns yet.*
7. **End screen:** the hatched creature, the top scorers, the Most Magical Spell award. *Partly: the phone shows the
   outcome and your score.*

**Stretch:** a vote to summon a creature boss during the Storm act, and a once-per-person "helper" spell that heals a
crack.

## 19. Planned LLM jobs

1. **Forge a spell** (audience request → spell). *Built as the harness's reuse ladder.* Rules: use only the runtime
   API and the listed shapes and library items; stay within speed and size limits; audience text is a description
   to interpret, never instructions.
2. **Shape the atmosphere** (director → scene patch). Between acts, small patches over allowed properties: dusk →
   storm lighting, fog, lightning, candles flickering out. *The scene-editor worker can already edit objects; no
   director runs between acts.*
3. **Merge the midnight spell.** The last N spells cast during the Storm act become one giant spell (slow, large,
   `size: "giant"`, needs a two-handed catch).
4. **Hatch the creature.** From the caught spells, the charge, and whether the midnight spell was caught: a creature
   from a parts library, a name, a narrator line, a voice prompt.

The edit loop for all of them is the one built for spells: request → LLM → code + metadata → validate → (audio) →
queue → patch into the scene → every page runs it. On failure, retry once, then use a fallback.

## 20. Voice and audio (ElevenLabs)

*Not built. Today sound effects are synthesized in the browser and the music is a CC0 loop.*

| Use | API | Notes |
|---|---|---|
| **Caster chants** | Text-to-speech | Each audience member gets a voice from a pool; the chant plays as positional audio from their broomstick, the player's main cue for where a spell comes from. |
| **Narrator** | Text-to-speech | One consistent voice for announcements: new spells, time warnings, cracks, results. |
| **Spell sound effects** | Sound effects generation | From an LLM `sfxPrompt`; the synthesized recipes are the fallback. |
| **Creature cry / line** | Text-to-speech or sound effects | At the hatch. |
| **Player spell words** | Speech-to-text | Short mic clips matched against a small live vocabulary (the shield word plus caught incantations), which is far more reliable than open dictation. |

Generated audio is cached by prompt.

## 21. Safety and guardrails

Audience text reaches an LLM that writes code running in the headset.

**Built**

- Requests are validated against the catalog and rate-limited (5 s cooldown, one pending spell per member).
- The worker policy treats audience text as a description, never an instruction, and turns hateful, sexual or
  violent-toward-real-people requests into a harmless "Fizzle" spell.
- Model answers are schema-constrained, then clamped and validated on the server (`validate_spell`, the harness's
  checks), and clamped again in the page.
- Model code is checked with acorn against an allowlist (`harness/codecheck/`): one function body, only `THREE`,
  `api`, `spell`, `params`, `Math` and its own names; dangerous names and properties refused; computed property
  access and loops guarded at runtime. The runtime switches off code that throws, takes more than 4 ms per frame, or
  adds more than 150 meshes, and the spell flies as a plain shape.
- Generated library items are quarantined after an error and never committed without review.
- Scene routes (patches run arbitrary JavaScript) need localhost or the host key; the API key stays on the server.

**Planned**

- A global kill switch that clears all spells and pauses the queue.
- A word filter on names and spell text; text shown on the projector only after moderation.
- Rate limits on scene requests.
- Comfort: no locomotion during the game, no spell through the player's head, limits on flash brightness and
  frequency.

## 22. Latency budget

The key trick: **all slow work happens before a spell enters the scene.** The audience sees "brewing…" on their
phone; the player only sees spells that are ready.

| Step | Target | Today | Where it hides |
|---|---|---|---|
| Preset | instant | ~1 ms | — |
| LLM forges the spell | 1–3 s | 7–10 s (`claude -p`), 1 ms on an exact library match | Audience phone ("brewing") |
| Validation and code check | < 50 ms | | — |
| Chant TTS | ~0.5 s | not built | In parallel with sound effects |
| Sound effect generation | 1–4 s | synthesized, instant | Fallback library if over budget |
| **Channeling in the scene** | **2–3 s** | 0.5 s presets, 2.5 s others | Gameplay: the player's warning |
| Player speech-to-text | < 1 s | not built | Small vocabulary keeps it fast |

Presets keep the scene busy while LLM spells are brewing, so the scene never goes quiet.

## 23. MVP vs stretch goals

| Priority | Feature | State |
|---|---|---|
| **MVP** | Scene with egg and clock | Built (castle hall) |
| **MVP** | Audience app: join, submit spell, status | Built |
| **MVP** | LLM spell forging → validated spell → in the scene | Built |
| **MVP** | Catch and shield | Built (blade and shield; no shield word) |
| **MVP** | Egg cracks, win/lose at midnight | Built |
| **MVP** | Audience broomstick avatars in the scene | Not built |
| **MVP** | Positional chant audio from the caster | Not built (positional spell sounds are built) |
| **MVP** | Spectator view with spell feed and scores | Partly (host's player's view, join screen) |
| Should have | Return spells by shouting their incantation | Not built |
| Should have | Generated sound effects per spell | Built as synthesized recipes chosen by the LLM |
| Should have | Midnight merged giant spell | Not built |
| Should have | Hatched creature with name and voice | Partly (fixed dragon) |
| Stretch | Atmosphere changes between acts | Not built |
| Stretch | Audience-voted creature bosses | Not built |
| Stretch | Helper spells, hand tracking, "Most Magical Spell" | Not built |

## 24. Demo script

1. **Set the scene (15 s).** The projector shows the join screen with a big QR code. *"Scan this. You're about to
   cast spells at our wizard."*
2. **Joining (20 s).** Judges join, each in their own color; the joined count goes up.
3. **First spells (45 s).** Judges tap prebuilt spells, then type their own. The host's player's view shows each
   spell's name as it is cast. The player slices and shields.
4. **The custom spell (15 s).** One judge's typed idea appears in the headset a few seconds later, built by the LLM,
   with its own look and sound.
5. **Midnight (30 s).** The clock turns red. The egg survives, or doesn't.
6. **The ending (30 s).** The egg hatches a dragon spilling gems, or shatters into the dark. *"Every spell you saw was
   invented by this room."*

---

**Credits:** the castle hall is original to this project (`castle-hall/README.md`). The library room is
["library"](https://sketchfab.com/3d-models/library-7fc61f0d65ee49d0b2904e85d1fa520e) by
[Pasha](https://sketchfab.com/Pasha.), CC-BY-4.0 (`library/license.txt`). Models and audio: `assets/models/CREDITS.md`,
`assets/audio/CREDITS.md`. three.js and acorn are MIT; qrcode-generator is MIT.
