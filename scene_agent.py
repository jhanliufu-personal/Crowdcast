"""Turns a chat message into scene-editing commands using Claude.

The browser sends the user's message plus a snapshot of the editable objects; Claude
answers with commands (add_object / update_object / remove_object), which are sent
back to the browser and applied to the three.js scene graph there.

Two backends, picked by WORLDTINDER_BACKEND (default: "api" if ANTHROPIC_API_KEY is set,
otherwise "claude-cli"):
  api         Claude API tool use. Fastest; needs an API key, which never leaves this process.
  claude-cli  Your Claude Code CLI in headless mode (claude -p), on your Claude subscription.
              Slower: every message starts a new CLI process (a few seconds of overhead).
"""

import json
import os
import shutil
import subprocess
import tempfile
import time

BACKEND = os.environ.get("WORLDTINDER_BACKEND") or ("api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-cli")
# Default per the project's Claude API guidance; set WORLDTINDER_MODEL=claude-haiku-5-5 to trade capability for speed.
MODEL = os.environ.get("WORLDTINDER_MODEL", "claude-opus-5-5")
# Low effort keeps simple edits fast; raise it for complicated multi-object requests.
EFFORT = os.environ.get("WORLDTINDER_EFFORT", "low")
# Models that support the server-side refusal fallback ("default" routes by refusal category).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

SHAPES = ["box", "sphere", "cylinder", "cone", "torus", "book"]
ANIMATIONS = ["none", "float", "spin", "orbit"]
VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}

TOOLS = [
    {
        "name": "add_object",
        "description": "Add a new object to the scene. Coordinates are meters: x right, y up (floor is y=0), z toward the starting view (-z is forward).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short unique, human-readable id, e.g. 'blue-sphere-1'."},
                "shape": {"type": "string", "enum": SHAPES},
                "color": {"type": "string", "description": "CSS hex color, e.g. '#3366ff'."},
                "size": {"type": "number", "description": "Approximate largest dimension in meters (0.05-2)."},
                "position": {**VEC3, "description": "[x, y, z] in meters."},
                "animation": {"type": "string", "enum": ANIMATIONS},
                "glow": {"type": "boolean", "description": "Make it glow and light up its surroundings (default false)."},
            },
            "required": ["name", "shape", "color", "size", "position", "animation"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_object",
        "description": "Change an existing object. Only include the fields that change.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the existing object to change."},
                "color": {"type": "string"},
                "size": {"type": "number"},
                "position": VEC3,
                "animation": {"type": "string", "enum": ANIMATIONS},
                "glow": {"type": "boolean"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_object",
        "description": "Remove an object from the scene. Use name '*' to remove every object you can edit.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
]

SYSTEM = """You edit a 3D WebXR scene: a life-size two-storey library hall that the user walks around in, \
on a Mac or in a VR headset. You can only add, change and remove the simple objects listed in the \
scene state; the room itself can't be edited.

Do what the user asks by calling the tools, as many as needed in one reply. Keep objects inside the \
room bounds, and place things relative to the user's position and facing direction when they say \
"in front of me", "to my left" and so on. Pick sensible sizes, colors and heights when unspecified \
(eye level is about 1.6 m above the user's floor). Animations: "float" bobs gently in place, \
"spin" turns in place, "orbit" circles about 1 m around the object's position. Along with the tool calls, write one short \
sentence saying what you did. If a request can't be done with these tools, say so briefly."""



def handle_chat(message, scene_state):
    """Return {"commands": [...], "text": str, "model": str, "ms": int} for one chat message."""
    prompt = f"Scene state:\n{json.dumps(scene_state)}\n\nRequest: {message}"
    return chat_cli(prompt) if BACKEND == "claude-cli" else chat_api(prompt)


_client = None


def chat_api(prompt):
    global _client
    if _client is None:
        import anthropic  # only needed for this backend
        _client = anthropic.Anthropic()
    request = dict(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM,
        tools=TOOLS,
        output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": prompt}],
    )
    betas = []
    if MODEL in FALLBACK_MODELS:
        betas.append("server-side-fallback-2026-07-01")
        request["fallbacks"] = "default"

    start = time.perf_counter()
    response = _client.beta.messages.create(betas=betas, **request)
    ms = round((time.perf_counter() - start) * 1000)

    if response.stop_reason == "refusal":
        return {"commands": [], "text": "The model declined that request.", "model": response.model, "ms": ms}

    commands = [{"name": b.name, "input": b.input} for b in response.content if b.type == "tool_use"]
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    return {"commands": commands, "text": text, "model": response.model, "ms": ms}


# The CLI has no custom tools in headless mode, so the same three tools become one JSON reply:
# a list of commands, each naming its tool and carrying that tool's fields.
CLI_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "One short sentence saying what you did."},
        "commands": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": [t["name"] for t in TOOLS]},
                    **{k: v for t in TOOLS for k, v in t["input_schema"]["properties"].items()},
                },
                "required": ["tool", "name"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["text", "commands"],
    "additionalProperties": False,
}

CLI_SYSTEM = SYSTEM + "\n\nTools (each command names one in its \"tool\" field):\n" + "\n".join(
    f"- {t['name']}: {t['description']} Fields: {', '.join(t['input_schema']['properties'])}"
    f" (required: {', '.join(t['input_schema']['required'])})."
    for t in TOOLS
)


def chat_cli(prompt):
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("The claude CLI isn't on PATH; install Claude Code or set ANTHROPIC_API_KEY.")
    cmd = [
        claude, "-p", prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(CLI_SCHEMA),
        "--system-prompt", CLI_SYSTEM,
        "--tools", "",               # no file, shell or web tools: it can only answer
        "--strict-mcp-config",       # and no MCP servers from your settings
        "--no-session-persistence",  # keep these calls out of your session history
        "--effort", EFFORT,
    ]
    if os.environ.get("WORLDTINDER_MODEL"):
        cmd += ["--model", os.environ["WORLDTINDER_MODEL"]]

    start = time.perf_counter()
    # Run outside the project so no CLAUDE.md or project settings are picked up.
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=tempfile.gettempdir())
    ms = round((time.perf_counter() - start) * 1000)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude CLI failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        raise RuntimeError(f"claude CLI error: {str(out.get('result'))[:300]}")

    reply = out["structured_output"]
    commands = []
    for c in reply.get("commands", []):
        c = dict(c)
        commands.append({"name": c.pop("tool"), "input": c})
    models = ", ".join(out.get("modelUsage", {})) or "claude CLI"
    return {
        "commands": commands,
        "text": reply.get("text", ""),
        "model": f"{models} via claude CLI (model {out.get('duration_api_ms')} ms)",
        "ms": ms,
    }
