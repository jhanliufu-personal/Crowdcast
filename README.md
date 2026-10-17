# The Midnight Egg 🥚✨

*A mixed-reality wizard duel for Meta Quest (WebXR) where the audience invents the spells.*

You are a young wizard on top of a castle tower, guarding a magical egg until midnight. The crowd around you, playing from their phones, types spells in plain language. An LLM turns each idea into living code inside the scene. You shield, catch, and throw those spells back by **shouting their names and flicking your wand**. If the egg survives until midnight, it hatches into a creature built from the spells you caught, so every round produces a different one.

> **Theme note:** the game is in the style of a classic wizarding-school fantasy (castle at night, wands, broomsticks, magical creatures), but every name, team, spell word, and character is original. This keeps the demo safe to show, record, and publish.

> **Status:** most of this document is the **design target**. What exists in the repo (WorldTinder) today is a working prototype of the core loop: *text in → LLM → edits to a live WebXR scene graph*. See [Current prototype](#current-prototype) for what is built and how to run it; sections below call out where the prototype differs from the plan.

---

## Table of contents

0. [Current prototype](#current-prototype)
1. [Core idea](#core-idea)
2. [Game design](#game-design)
3. [Audience web app](#audience-web-app)
4. [Architecture](#architecture)
5. [The scene graph and the scene code](#the-scene-graph-and-the-scene-code)
6. [How the LLM edits the scene](#how-the-llm-edits-the-scene)
7. [Voice and audio (ElevenLabs)](#voice-and-audio-elevenlabs)
8. [Safety and guardrails](#safety-and-guardrails)
9. [Latency budget](#latency-budget)
10. [Network protocol](#network-protocol)
11. [Project structure](#project-structure)
12. [Tech stack and setup notes](#tech-stack-and-setup-notes)
13. [MVP vs stretch goals](#mvp-vs-stretch-goals)
14. [Demo script](#demo-script)

---

## Current prototype

Three files, no build step:

| File | What it does |
|---|---|
| `index.html` | The WebXR scene (Three.js 0.170 from a CDN import map). Loads a glTF room (`library/scene.gltf` by default, or `?scene=path.gltf`), centers it with the floor at y=0, and keeps a set of **editable objects** in the scene graph. Also hosts a chat box that sends text to the server and applies the returned edits. |
| `serve.py` | Python standard-library HTTP server. Serves the static files and handles `POST /api/chat`. `--https` generates a self-signed cert into `.certs/` so the Quest can connect over Wi-Fi. |
| `scene_agent.py` | Turns one chat message plus the current scene state into scene-editing commands using Claude (see [How the LLM edits the scene](#how-the-llm-edits-the-scene)). |

**What works today**

- Walk around the room: drag to look + WASD/arrows (Q/E down/up) on desktop; left stick to move and right stick to snap-turn in VR.
- Add objects by hand: trigger in VR, or **B** on desktop, spawns a floating book in front of you.
- Type a request in the chat box ("put three blue spheres in front of me", "make the cube green", "remove the books"). Claude replies with commands that add, update, or remove objects, and the page applies them immediately and logs the timing and model used.
- `?emulate` loads Meta's IWER emulated Quest 3 (with on-screen DevUI controls) so "Enter VR" works on a laptop with no headset.
- From the browser console, `sceneState()` shows exactly what is sent to the LLM and `applyCommands([...])` applies edits without calling it.

**Not built yet:** the castle tower, egg, clock, spells, wand/shield input, separate audience/spectator apps, teams and scoring, WebSocket protocol, ElevenLabs audio, and generated code modules. The chat box is the stand-in for the audience web app: it lives on the same page as the scene rather than on a phone.

**Run it**

```bash
conda env create -f environment.yml   # Python 3.12 + the anthropic package
conda activate worldtinder
export ANTHROPIC_API_KEY=...           # optional, see backends below
python serve.py                        # http://localhost:8000
python serve.py --https                # https://<lan-ip>:8443 for the Quest over Wi-Fi
```

Without `--https`, the Quest can still reach the server over USB with `adb reverse tcp:8000 tcp:8000` and then `http://localhost:8000` (WebXR needs localhost or HTTPS).

**LLM settings** (environment variables read by `scene_agent.py`)

| Variable | Default | Meaning |
|---|---|---|
| `WORLDTINDER_BACKEND` | `api` if `ANTHROPIC_API_KEY` is set, else `claude-cli` | `api` calls the Claude API with tool use (fastest). `claude-cli` runs the Claude Code CLI headless (`claude -p`) on your subscription, with all of its tools and MCP servers disabled; slower because each message starts a process. |
| `WORLDTINDER_MODEL` | `claude-opus-5-5` | Model id, e.g. `claude-haiku-5-5` for lower latency. |
| `WORLDTINDER_EFFORT` | `low` | Reasoning effort; raise it for complicated multi-object requests. |

**Credits:** the room model is ["library"](https://sketchfab.com/3d-models/library-7fc61f0d65ee49d0b2904e85d1fa520e) by [Pasha](https://sketchfab.com/Pasha.), licensed under [CC-BY-4.0](http://creativecommons.org/licenses/by/4.0/) (see `library/license.txt`).

---

## Core idea

| Element | What it is |
|---|---|
| **Player** | One person in a Quest headset, standing in place, with a wand (right controller or hand) and a shield hand (left). |
| **Audience** | Anyone nearby with a phone. They join a web app by QR code, get sorted into a team, and send spells as free text. |
| **LLM** | The game's "spell forge." It turns audience text into code that runs in the scene, merges spells for the finale, and designs the hatched creature. |
| **Scene graph** | The single source of truth for everything in the world. All rendering, gameplay, and LLM edits go through it. |
| **ElevenLabs** | Voices for spell incantations, the narrator, and the creature; generated sound effects; speech-to-text for the player's spell words. |
| **Spectator screen** | A laptop or projector showing a third-person view of the tower, the spell feed, the clock, and team scores. |

**Why VR:** spells come from **all 360°**, including behind you, at different depths. You hear each caster chant from their direction before the spell fires, so you turn, track, and react with your whole body. This doesn't work on a flat screen.

---

## Game design

### The goal

- An **egg floats in a cradle at chest height** just in front of the player. Every audience spell is aimed at it.
- A **tower clock** counts down to midnight. One round lasts about **3 minutes**.
- **Win:** the egg is still intact at midnight. It hatches into a creature designed from the spells you caught.
- **Lose:** the egg takes too many hits (default: **5 cracks**) and breaks open early, releasing a tiny, grumpy creature. This is a funny failure, not a punishing one.
- **Charge meter:** each caught spell charges the egg. More charge (and more *different* spells caught) produces a bigger, more elaborate creature.

### The player's three actions

| Action | Input | Effect | Trade-off |
|---|---|---|---|
| **Shield** | Shout the shield word (**"Aegis!"**) and raise your left hand | A glowing barrier appears in front of the shield hand and blocks spells | Safe, but earns no charge |
| **Catch** | Flick the wand at a spell in flight | The spell shrinks into a glowing orb that orbits the player and charges the egg | Needs aim and timing; a miss means the egg gets hit |
| **Return** | Shout a caught spell's incantation and flick toward an audience avatar | Fires that spell back at the caster's broomstick | Uses up the orb's charge; the caster's team loses points and their phone buzzes |

The central decision is **shield safely vs. catch for progress**. It's easy to understand and hard to master.

### Spells have names

When an audience member writes *"a teapot that screams and chases them,"* the LLM produces:

- A **name**: *Shrieking Kettle*
- An **incantation**: *"Kettlenox!"*
- The **code** that makes it fly, look, and sound the way it was described

If the player catches it, *"Kettlenox!"* becomes a word they can shout to send it back. The player's vocabulary grows during the round, and every word in it comes from the crowd.

### The round in three acts

| Act | Time | What happens |
|---|---|---|
| **Dusk** | 0:00–1:00 | A few spells at a time, mostly from in front. The player learns to shield and catch. |
| **Storm** | 1:00–2:30 | Volleys from every direction, spells from behind, weather effects, audience-summoned creatures (stretch). |
| **Midnight** | final 30 s | The audience's last spells are **merged by the LLM into one giant spell**. It's huge and slow, and the player must catch it with a two-handed wand motion. Success gives a "legendary" hatch; failure costs 2 cracks. |

### The hatch (the payoff)

At midnight, the LLM receives the list of spells the player caught and designs a creature from them. For example, fire + paper birds + kettle could produce a **flame-winged teapot dragon**. The creature is assembled from a parts library (body, head, wings, tail, accessories, colors, particles), receives a name and a voice line from the narrator, and circles the tower. The spectator screen shows it with the credits: *"Hatched from spells by Maya, Theo and Priya."*

### Teams and scoring

- On joining, the audience is sorted by an **enchanted mirror** into one of four original teams:

  | Team | Color |
  |---|---|
  | Ember | Crimson |
  | Tide | Blue |
  | Thorn | Green |
  | Gale | Gold |

- **Team points:** +10 when your spell cracks the egg; −5 when the player returns a spell at one of your team's casters.
- **Awards at the end:** winning team, plus a **"Most Magical Spell"** chosen by the narrator (LLM), which rewards creativity over spamming fireballs.
- Caught spells become part of the creature, which gives the audience another reason to be creative.

---

## Audience web app

A mobile-first page served by the game server. No install, no account.

**Flow**

1. **Join:** scan the QR code on the spectator screen, enter a display name.
2. **Sorting:** a short animation where the enchanted mirror reveals the team. The player's broomstick avatar appears around the tower in team colors.
3. **Cast:** a text box (max ~120 characters) with example chips ("a swarm of paper birds", "lightning that zigzags", "a cursed teapot") and a **Cast** button.
4. **Brewing → Channeling → Result:** live status for their spell.
   - *Brewing* – the LLM is forging it (the phone shows a cauldron animation).
   - *Channeling* – their avatar is chanting in the scene.
   - *Result* – **Hit!**, **Caught!**, or **Shielded**, with the spell's name and incantation.
5. **Cooldown:** a ring timer (default **15 s** per person) to keep things fair and limit LLM usage.
6. **Getting hit:** if the player returns a spell at you, your phone buzzes (Vibration API where supported) and the screen flashes your team color.
7. **End screen:** the hatched creature, team results, and the Most Magical Spell award.

**Stretch:** a vote to summon a creature boss during the Storm act, and a "helper" spell each audience member can use once to heal a crack.

---

## Architecture

```
┌─────────────────┐        WebSocket        ┌──────────────────────────┐
│  Audience app   │ ─── spell text ───────▶ │       Game server        │
│  (phones)       │ ◀── status, results ─── │  (Python, from serve.py) │
└─────────────────┘                         │                          │
                                            │  • session & scores      │
┌─────────────────┐                         │  • spell queue           │──▶ LLM API
│  Headset client │ ◀── spell modules,      │  • LLM orchestration     │
│  (Quest, WebXR) │     scene patches,      │  • code validation       │──▶ ElevenLabs
│                 │     audio URLs          │  • audio generation      │    (TTS, SFX, STT)
│  scene graph +  │ ─── events (hit, catch, │  • relay to spectators   │
│  runtime        │     voice audio) ─────▶ └──────────────────────────┘
└─────────────────┘                                     │
        │ snapshots                                     │
        ▼                                               ▼
┌─────────────────┐                         ┌──────────────────────────┐
│ Spectator view  │ ◀── same scene graph, third-person camera, feed, scores
└─────────────────┘
```

**Authority split**

- The **headset** is the authority for the simulation: movement, collisions, catches, hits. This keeps gameplay responsive for the player.
- The **server** is the authority for the session: players, teams, scores, the spell queue, and all generated code and audio.
- The **spectator view** runs the same scene code and mirrors the headset's scene graph through periodic snapshots and events.

**Today:** there is one client, `index.html`, which is the scene and the text input at once. It sends `{ message, scene }` to `POST /api/chat`; the server calls Claude and returns commands; the browser applies them to its own scene graph. The server holds no state between requests: the browser owns the scene and sends a fresh snapshot with every message.

```
index.html ── POST /api/chat { message, scene } ──▶ serve.py ──▶ scene_agent.py ──▶ Claude (API or claude CLI)
   ▲                                                                                  │
   └──────────── { commands, text, model, ms } ◀──────────────────────────────────────┘
```

---

## The scene graph and the scene code

Everything in the world is a node in one scene graph, and everything that *happens* is a function in the scene code. This is the center of the project.

### Scene graph

A tree of nodes. Each node is serializable, so it can be sent to the spectator view, logged, and described to the LLM.

```json
{
  "id": "spell-42",
  "prefab": "teapot",
  "parent": "spells",
  "transform": { "position": [3.2, 2.1, -4.0], "rotation": [0, 90, 0], "scale": 0.4 },
  "material": { "color": "#ff7a2f", "glow": 0.8 },
  "tags": ["spell", "catchable"],
  "behavior": "shrieking-kettle",
  "meta": { "caster": "maya", "team": "ember" }
}
```

Fixed top-level groups:

| Group | Contents | Who edits it |
|---|---|---|
| `world` | Tower, sky, candles, clock, weather | Base scene; LLM can patch atmosphere (lighting, fog, weather) |
| `egg` | Egg, cradle, crack and charge visuals | Game logic only |
| `player` | Wand, shield, orbiting caught-spell orbs | Game logic only |
| `casters` | Audience broomstick avatars around the tower | Game logic (one per audience member) |
| `spells` | Spells in flight | **LLM-generated spell code** |
| `creatures` | Bosses and the hatched creature | **LLM-generated creature code** |

**Today:** the scene graph is a plain Three.js scene. The loaded room model is static and can't be edited. Everything editable is a named `THREE.Group` kept in an `editables` map, described by a small spec that is both what the LLM sees and what its commands change:

```json
{ "name": "orange-cube", "shape": "box", "color": "#ff5533", "size": 0.25,
  "position": [0, 1.4, 0], "animation": "orbit", "glow": true }
```

- `shape`: `box`, `sphere`, `cylinder`, `cone`, `torus`, `book` (built at 1 m, then scaled to `size` meters).
- `animation`: `none`, `float` (bob and slow turn), `spin` (turn in place), `orbit` (circle around `position`).
- `glow`: emissive material plus a point light in the object's color.

The scene state sent with each chat message adds the room's size, the user's head position and facing direction, and the allowed shapes and animations, so the LLM can place things "in front of me" or "to my left". There are no groups, tags, or `meta` fields yet.

### Scene code (HTML + JavaScript)

*Planned; today the LLM uses the three structured commands described in the next section instead of writing code.*

The scene is a single HTML page (`headset/index.html`; currently `index.html` at the repo root) containing the base scene plus a **runtime API**: a small library of functions for creating, animating, and editing nodes. Spells and creatures are short JavaScript modules that call only this API.

**Runtime API (what the LLM is allowed to use)**

| Category | Functions |
|---|---|
| Create / remove | `spawn(prefab, opts)`, `destroy(node)`, `group(nodes)` |
| Appearance | `color(node, hex)`, `glow(node, amount)`, `scale(node, s, ms)`, `trail(node, opts)`, `particles(node, preset)` |
| Movement | `move(node, path)` with `paths.straight`, `paths.homing`, `paths.zigzag`, `paths.spiral`, `paths.orbit`, `paths.arc`, `paths.boomerang` |
| Behavior | `split(node, n, opts)`, `cloak(node, untilDistance)`, `accelerate(node, factor, ms)`, `wobble(node, opts)` |
| Timing | `after(ms, fn)`, `every(ms, fn, times)` |
| Audio | `sound(node, sfxId, opts)`, `speak(node, voiceLineId)` |
| Context (read-only) | `caster`, `target` (the egg), `player`, `rand()` (seeded) |

**Example generated spell module**

```js
// spells/shrieking-kettle.js — generated by the LLM, validated before use
export default {
  name: "Shrieking Kettle",
  incantation: "Kettlenox",
  channelMs: 2500,
  power: 2,              // charge gained when caught
  size: "medium",

  cast({ api, caster, target }) {
    const kettle = api.spawn("teapot", {
      at: caster.wandTip, scale: 0.4, color: "#ff7a2f", glow: 0.8,
      tags: ["spell", "catchable"],
    });
    api.trail(kettle, { color: "#ffd2a8" });
    api.sound(kettle, "sfx:shrieking-kettle", { loop: true });
    api.move(kettle, api.paths.homing(target, { speed: 2.0, turnRate: 1.2 }));
    api.after(3000, () => api.accelerate(kettle, 1.8, 500)); // final lunge
    return kettle;
  },
};
```

Each spell renders identically on the headset and the spectator view because both load the same module and use the same seed.

---

## How the LLM edits the scene

The LLM never draws pixels or touches the renderer directly.

### Today: structured commands

`scene_agent.py` gives Claude three tools and a system prompt describing the room:

| Tool | Fields | Effect in `index.html` |
|---|---|---|
| `add_object` | `name`, `shape`, `color`, `size`, `position`, `animation`, optional `glow` | Creates a new editable object (a `-2`, `-3`… suffix is added if the name is taken) |
| `update_object` | `name` plus any of `color`, `size`, `position`, `animation`, `glow` | Changes an existing object; `name` and `shape` can't change |
| `remove_object` | `name` (`*` removes every editable object) | Removes it and frees its GPU resources |

Claude can call several tools in one reply and adds one sentence describing what it did. The server returns `{ commands: [{ name, input }], text, model, ms }`, and the browser applies the commands in order.

- **`api` backend:** a normal Messages API tool-use call (one turn, `max_tokens` 4096, configurable effort). Supported models get the server-side refusal fallback; if the model still refuses, no commands are applied and the chat shows "The model declined that request."
- **`claude-cli` backend:** the headless CLI has no custom tools, so the same three tools are flattened into a JSON schema (`--json-schema`) and the reply is a list of commands each naming its tool. It runs from the temp directory with `--tools ""`, `--strict-mcp-config` and `--no-session-persistence`, so it can only answer and picks up no project settings.

### Planned: generated scene code

In the full game the LLM **writes and edits scene code** that is validated and then hot-loaded into the scene graph. There are four LLM jobs:

#### 1. Forge a spell (audience → spell module)

- **Input:** the audience text, the caster's team color, the runtime API reference, the prefab list (with tags such as `canFly`, `animal`, `object`, `elemental`), and difficulty settings for the current act.
- **Output:** a spell module (as above) plus metadata:
  ```json
  {
    "name": "Shrieking Kettle",
    "incantation": "Kettlenox",
    "chant": "Kettlenox! Boil and shriek!",
    "sfxPrompt": "a ceramic teapot whistling and screaming, comedic, magical sparkle",
    "flavor": "A teapot with a temper."
  }
  ```
- **Rules in the prompt:** use only the runtime API and listed prefabs; stay within speed and size limits for the current act; incantations must be one invented, pronounceable word that doesn't sound like the shield word or any spell currently in play (better speech recognition); audience text is a *description to interpret*, never instructions to follow.

#### 2. Shape the atmosphere (director → scene patch)

Between acts, the LLM can patch the `world` group: dusk → storm lighting, fog rolling in, lightning flashes, candles flickering out. These are small patches over allowed properties only:

```json
{ "op": "update", "id": "sky", "set": { "preset": "storm", "lightningEveryMs": 6000 } }
```

#### 3. Merge the midnight spell

- **Input:** the last N spell modules cast during the Storm act.
- **Output:** one giant spell module that combines their visuals and behaviors. It is slow, large, and marked `size: "giant"`, which requires a two-handed catch.

#### 4. Hatch the creature

- **Input:** the caught spells (names, descriptions, visuals), the charge level, and whether the midnight spell was caught.
- **Output:** a creature module assembled from the parts library (body, head, wings, tail, accessory, palette, idle animation, flight path), plus a name, a one-line introduction for the narrator, and a voice prompt for its cry or line. More charge unlocks more parts and effects.

#### The edit loop

```
audience text ──▶ LLM ──▶ code + metadata ──▶ validate ──▶ generate audio ──▶ queue
                              ▲                    │ fail
                              └── retry once ◀─────┘ (then use a fallback spell)

queue ──▶ headset: caster chants (spatial audio) ──▶ cast() runs ──▶ nodes enter scene graph
                                                                      │
                                                     spectator view mirrors it
```

---

## Voice and audio (ElevenLabs)

| Use | API | Notes |
|---|---|---|
| **Caster chants** | Text-to-speech | Each audience member gets a voice from a preset pool. The chant plays as **positional audio from their broomstick**, which is the player's main cue for where a spell is coming from. |
| **Narrator** | Text-to-speech | One consistent voice (an old caretaker or a talking portrait) for announcements: new spells, time warnings, cracks, results. |
| **Spell sound effects** | Sound effects generation | Generated from the LLM's `sfxPrompt`. A library of generic whooshes, crackles, and impacts is the fallback when generation is slow. |
| **Creature cry / line** | Text-to-speech or sound effects | Played at the hatch. |
| **Player spell words** | Speech-to-text | Short audio clips from the headset mic, fuzzy-matched against a **small live vocabulary**: the shield word plus the incantations of currently caught orbs. Matching a handful of known words is far more reliable than open dictation. |

Generated audio is cached by prompt so repeated narrator lines and common sound effects are reused.

---

## Safety and guardrails

Audience text goes into an LLM that writes code, so this matters even at a hackathon.

**Today** the LLM can't run code at all; it can only emit the three commands. The guardrails are: tool schemas with enums for `shape` and `animation` and no extra fields; the browser re-validates every command (`cleanSpec` in `index.html`: colors must parse, `size` is clamped to 0.03–3 m, positions must be three finite numbers, unknown fields are dropped); the API key stays in the server process; and the CLI backend runs with no tools or MCP servers. Positions are not yet clamped to the room, and there is no rate limit, moderation step, or kill switch.

**Generated code** (planned)

- Parsed into an AST (for example, with `acorn`) and checked against an **allowlist**: only `api.*` calls, the context objects, basic math, and arrow functions.
- Rejected outright if it contains `window`, `document`, `globalThis`, `fetch`, `import`, `eval`, `Function`, `while`, prototype access, or any identifier not on the allowlist.
- Runs with a **frozen API object** and inside `try/catch`. A spell that throws is removed and replaced with a fallback.
- **Budgets per spell:** max nodes spawned, max lifetime (e.g., 10 s), max speed, max scale, max particles. The runtime enforces these even if the code tries to exceed them.
- A global **kill switch** on the server clears all spells and pauses the queue.

**Content**

- The LLM prompt includes moderation: offensive or harmful spell ideas are turned into a harmless "fizzle" spell, and the audience member sees a playful message.
- A simple word filter on display names and spell text.
- Spell text appears on the spectator screen only after it has passed moderation.

**Comfort**

- The player never moves through the world (no locomotion), which avoids motion sickness for first-time VR users. *(The prototype still has thumbstick movement and snap turning for exploring the test room; remove it for the game.)*
- No spell may fly through the player's head, and flashes are limited in brightness and frequency.

---

## Latency budget

The key trick: **all slow work happens before a spell enters the scene.** The audience sees "Brewing…" on their phone; the player only sees spells that are ready.

| Step | Target | Where it hides |
|---|---|---|
| LLM forges the spell | 1–3 s | Audience phone ("Brewing…") |
| Validation | < 50 ms | — |
| Chant TTS | ~0.5 s | In parallel with sound effect generation |
| Sound effect generation | 1–4 s | Fallback library if over budget |
| **Channeling in the scene** | **2–3 s** | Gameplay: this is the player's warning, not a wait |
| Player speech-to-text | < 1 s | The flick happens with the word; small vocabulary keeps it fast |

The server keeps a few **pre-forged fallback spells** ready so the scene never goes quiet, even if the LLM or audio APIs are slow.

---

## Network protocol

**Today** there is a single HTTP endpoint and no WebSocket:

| Request | Body | Response |
|---|---|---|
| `POST /api/chat` | `{ message, scene }` (scene = the `sceneState()` snapshot) | `200 { commands, text, model, ms }` or `500 { error }` (shown in the chat box) |

All other paths are static files, served with `Cache-Control: no-store` so edits show up on reload.

**Planned:** all clients connect to the server over WebSocket (`/ws?role=headset|audience|spectator`).

| Event | Direction | Payload |
|---|---|---|
| `audience:join` | audience → server | `{ name }` |
| `audience:sorted` | server → audience | `{ id, team, color }` |
| `spell:submit` | audience → server | `{ text }` |
| `spell:status` | server → audience | `{ spellId, status: "brewing" \| "channeling" \| "hit" \| "caught" \| "shielded" \| "fizzled" }` |
| `spell:ready` | server → headset, spectator | `{ spellId, casterId, code, meta, audio: { chant, sfx } }` |
| `scene:patch` | server → headset, spectator | `{ ops: [...] }` (atmosphere edits) |
| `scene:snapshot` | headset → server → spectator | compact scene graph state, ~10 Hz |
| `game:event` | headset → server | `{ type: "hit" \| "catch" \| "shield" \| "return", spellId, targetCasterId? }` |
| `voice:clip` | headset → server | short audio clip for speech-to-text |
| `voice:match` | server → headset | `{ word, action: "shield" \| "return", spellId? }` |
| `game:state` | server → all | `{ act, clock, cracks, charge, scores }` |
| `audience:buzz` | server → audience | `{ reason: "returned", by: spellName }` |
| `game:hatch` | server → all | `{ creatureCode, meta, audio }` |

---

## Project structure

**Today**

```
WorldTinder/
├── index.html          # WebXR scene + chat box (Three.js, no build step)
├── serve.py            # static server + POST /api/chat, optional self-signed HTTPS
├── scene_agent.py      # chat message + scene state → Claude → commands
├── environment.yml     # conda env: Python 3.12, anthropic
├── library/            # test room: scene.gltf, scene.bin, textures/, license.txt (CC-BY-4.0)
└── .claude/launch.json # preview config that runs serve.py
```

**Planned** (sketched before the prototype; the server is now Python, so the `server/` files would be Python modules growing out of `serve.py` and `scene_agent.py`)

```
midnight-egg/
├── server/
│   ├── index.js              # HTTP + WebSocket server, serves all three clients
│   ├── session.js            # players, teams, scores, act timer
│   ├── queue.js              # spell queue, cooldowns, fallbacks
│   ├── llm/
│   │   ├── prompts/          # forge.md, director.md, merge.md, hatch.md
│   │   ├── forge.js          # audience text → spell module
│   │   ├── director.js       # atmosphere patches between acts
│   │   ├── merge.js          # midnight giant spell
│   │   └── hatch.js          # creature design
│   ├── audio/elevenlabs.js   # TTS, sound effects, speech-to-text, cache
│   └── sandbox/validate.js   # AST allowlist, budget checks
├── shared/
│   ├── protocol.js           # event names and payload shapes
│   ├── prefabs.json          # prefab list with tags (fed to the LLM)
│   ├── runtime-api.md        # API reference (fed to the LLM)
│   └── fallback-spells/      # hand-written spells for when generation fails
├── headset/
│   ├── index.html            # the scene: tower, egg, sky, casters
│   ├── runtime/              # api.js, paths.js, loader.js, budgets.js
│   └── input/                # wand.js (flick detection), shield.js, voice.js (mic capture)
├── spectator/index.html      # third-person camera, spell feed, scoreboard, QR code
├── audience/index.html       # phone app
└── assets/                   # models (tower, egg, prefabs, creature parts), fallback sounds
```

---

## Tech stack and setup notes

- **Rendering:** Three.js (0.170) with WebXR, loaded as ES modules from jsDelivr through an import map, so there is no bundler or build step. Addons in use: `GLTFLoader`, `OrbitControls` (desktop look-around), `VRButton`, `RoomEnvironment`.
- **Testing without a headset:** `?emulate` loads IWER's emulated Meta Quest 3 and its DevUI from esm.sh.
- **Server:** Python 3.12 standard library (`http.server.ThreadingHTTPServer`), set up with conda (`environment.yml`). The only dependency is `anthropic`, imported lazily so the viewer still works without it. The planned WebSocket protocol will need a WebSocket library on top of this.
- **Validation:** today, JSON-schema tool inputs plus `cleanSpec` in the browser. Once the LLM writes code, `acorn` (in the browser or a small Node helper) would parse it for the allowlist check.
- **AI:** Claude, through the API (tool use) or the Claude Code CLI; default `claude-opus-5-5` at low effort, configurable per [LLM settings](#current-prototype). The plan is a fast model for spell forging and a stronger model for merge and hatch; ElevenLabs for TTS, sound effects, and speech-to-text.
- **HTTPS is required** off localhost: the Quest browser only allows WebXR and microphone access on secure origins. `python serve.py --https` creates a self-signed certificate (accept the warning in the headset); alternatively use `adb reverse` over USB, or a tunnel (e.g., `cloudflared` or `ngrok`) so phones can reach the server too.
- **Input:** controllers are the safest for flick detection (velocity of the wand tip); hand tracking is a stretch goal. Haptic pulses on catch and shield. Today controllers only show a pointer line, spawn a book on trigger, and drive movement and snap turning with the sticks.
- **API keys** stay on the server only; clients never call the LLM or ElevenLabs directly. (True today: the browser only talks to `/api/chat`.)

---

## MVP vs stretch goals

| Priority | Feature |
|---|---|
| **MVP** | Tower scene with egg, clock, and audience broomstick avatars |
| **MVP** | Audience app: join, team sorting, submit spell, status updates |
| **MVP** | LLM spell forging → validated module → spell in the scene graph |
| **MVP** | Catch (wand flick) and shield (left hand + shield word) |
| **MVP** | Egg cracks, charge meter, win/lose at midnight |
| **MVP** | Positional chant audio from the caster's avatar |
| **MVP** | Spectator view with spell feed and scores |
| Should have | Return spells by shouting their incantation |
| Should have | Generated sound effects per spell |
| Should have | Midnight merged giant spell |
| Should have | Hatched creature with name and voice |
| Stretch | Atmosphere changes between acts (director) |
| Stretch | Audience-voted creature bosses during the Storm act |
| Stretch | Helper spells, hand tracking, "Most Magical Spell" award |

---

## Demo script

1. **Set the scene (15 s).** Spectator screen shows the tower at dusk and a big QR code. *"Scan this. You're about to cast spells at our wizard."*
2. **Sorting (20 s).** Judges join and get sorted into teams; their broomsticks appear around the tower on the screen.
3. **First spells (45 s).** Judges type spells. The screen shows each spell's name and incantation as the caster chants it. The player turns toward the voice, catches, shields.
4. **The return (15 s).** The player shouts a judge's own incantation back and hits their broomstick. The judge's phone buzzes.
5. **Midnight (30 s).** The LLM merges the crowd's spells into one giant spell. The player catches it with both hands.
6. **The hatch (30 s).** The egg hatches into a creature made from the judges' spells, with a name, a voice, and credits. *"Every round hatches something new."*