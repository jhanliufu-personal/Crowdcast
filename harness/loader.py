"""Reads the harness's instruction files (orchestrator.md, workers/, tools/, tags.md, runtime/spell-api.md).

Each file is Markdown with an optional frontmatter block of `key: value` lines between `---` lines. Values are
read as JSON when they parse (numbers, true/false, ["a", "b"]), as a list for [a, b], and as text otherwise.
Tool files also hold their JSON schema in a ```json block.

Two files hold many short entries, one `## name` section each:
    tags.md                   settings lines (applies_to, weight, sources) at the start of a section, then text
    tools/scene-commands.md   one scene command per section: its description, then its schema

The files are read again whenever one of them changes, so they can be edited while the server runs.
"""

import copy
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.S)
SECTION = re.compile(r"^## +(\S+)[ \t]*$", re.M)
TAG_SETTING = re.compile(r"^(applies_to|weight|sources):(.*)$")


def parse_value(text):
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    if text.startswith("[") and text.endswith("]"):
        return [part.strip().strip("\"'") for part in text[1:-1].split(",") if part.strip()]
    return text


def parse_md(text):
    """(frontmatter dict, body) for one instruction file."""
    meta, body = {}, text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if ":" in line and not line.lstrip().startswith("#"):
                    key, value = line.split(":", 1)
                    meta[key.strip()] = parse_value(value)
            body = text[end + 4:].lstrip("\n")
    return meta, body.strip()


def sections(text):
    """[(name, text)] for each `## name` section; what comes before the first section is left out."""
    marks = list(SECTION.finditer(text))
    return [(m.group(1), text[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(text)].strip())
            for i, m in enumerate(marks)]


@dataclass
class Doc:
    name: str
    meta: dict
    body: str
    schema: dict = field(default=None)


class HarnessFiles:
    def __init__(self, root=HERE):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._stamp = None
        self._load()

    def _paths(self):
        return sorted(
            [self.root / "orchestrator.md", self.root / "runtime" / "spell-api.md", self.root / "tags.md"]
            + [p for d in ("workers", "tools") for p in (self.root / d).glob("*.md")]
        )

    def _current_stamp(self):
        return tuple((str(p), p.stat().st_mtime_ns) for p in self._paths() if p.exists())

    def fresh(self):
        """Reload if any file changed since the last load. Returns self."""
        stamp = self._current_stamp()
        if stamp != self._stamp:
            with self._lock:
                if stamp != self._stamp:
                    self._load()
        return self

    def _load(self):
        stamp = self._current_stamp()
        read = lambda p: parse_md(p.read_text(encoding="utf-8"))
        meta, body = read(self.root / "orchestrator.md")
        self.settings, self.orchestrator = meta, body
        self.spell_api = (self.root / "runtime" / "spell-api.md").read_text(encoding="utf-8")
        self.workers, self.tools, self.tags = {}, {}, {}
        for path in (self.root / "workers").glob("*.md"):
            meta, body = read(path)
            self.workers[path.stem] = Doc(path.stem, meta, body)
        self.policy = self.workers.pop("_policy").body if "_policy" in self.workers else ""
        for path in (self.root / "tools").glob("*.md"):
            meta, body = read(path)
            if path.name == "scene-commands.md":
                for name, text in sections(body):
                    self.tools[name] = _tool(name, {"name": name, "kind": "command"}, text)
            else:
                name = meta.get("name", path.stem)
                self.tools[name] = _tool(name, meta, body)
        tags_path = self.root / "tags.md"
        for name, text in sections(tags_path.read_text(encoding="utf-8") if tags_path.exists() else ""):
            meta, lines = {}, text.splitlines()
            while lines and (m := TAG_SETTING.match(lines[0].strip())):
                meta[m.group(1)] = parse_value(m.group(2))
                lines.pop(0)
            self.tags[name] = Doc(name, meta, "\n".join(lines).strip())
        self._stamp = stamp

    def setting(self, key, default=None):
        return self.settings.get(key, default)

    def worker(self, name):
        return self.workers[name]

    def output_schema(self, name, catalog):
        """The JSON schema a worker's answer must follow, with {catalog.*} placeholders filled in."""
        if name == "edit_scene":
            return self._edit_scene_schema()
        tool = self.tools[name]
        schema = copy.deepcopy(tool.schema)
        if "extends" in schema:
            base = copy.deepcopy(self.tools[schema.pop("extends")].schema)
            base["properties"].update(schema.get("properties", {}))
            schema = base
        return fill(schema, {"catalog.motions": sorted(catalog.motions), "catalog.shapes": list(catalog.shapes)})

    def output_doc(self, name):
        """The Markdown explanation of an output format (for the worker's instructions)."""
        if name == "edit_scene":
            return "\n\n".join(f"### {t.name}\n{t.body}" for t in self.command_tools())
        tool = self.tools[name]
        parts = [tool.body]
        if tool.meta.get("extends"):
            parts.insert(0, self.tools[tool.meta["extends"]].body)
        return "\n\n".join(parts)

    def command_tools(self):
        return [t for t in sorted(self.tools.values(), key=lambda t: t.name) if t.meta.get("kind") == "command"]

    def _edit_scene_schema(self):
        """One answer holding several commands: each names its tool and carries that tool's fields."""
        tools = self.command_tools()
        fields = {}
        for t in tools:
            fields.update(t.schema["properties"])
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "commands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"tool": {"type": "string", "enum": [t.name for t in tools]}, **fields},
                        "required": ["tool", "name"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["text", "commands"],
            "additionalProperties": False,
        }

    def command_schema(self, tool_name):
        tool = self.tools.get(tool_name)
        return tool.schema if tool and tool.meta.get("kind") == "command" else None


def _tool(name, meta, text):
    match = JSON_BLOCK.search(text)
    schema = json.loads(match.group(1)) if match else None
    return Doc(name, meta, JSON_BLOCK.sub("", text).strip(), schema)


def fill(node, values):
    """Replace "{key}" strings in a schema with the matching value."""
    if isinstance(node, dict):
        return {k: fill(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [fill(v, values) for v in node]
    if isinstance(node, str) and node.startswith("{") and node.endswith("}") and node[1:-1] in values:
        return values[node[1:-1]]
    return node
