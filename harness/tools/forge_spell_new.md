---
name: forge_spell_new
kind: output
extends: forge_spell
---
The same spell object as `forge_spell`, plus two optional fields for new library items:

| Field | Meaning |
|---|---|
| `new_object` | A new thing to throw. Set `object` to its `id` to use it. |
| `new_behaviors` | 0-2 new effects. Add `{"id": <its id>, "params_json": "{}"}` to `behaviors` to use one. |

Each new item has:

| Field | Meaning |
|---|---|
| `id` | Short kebab-case name, e.g. `teapot` or `zigzag-sparks`. |
| `description` | One sentence: what it is or does. Later models search this text, so name the thing plainly. |
| `tags` | 2-5 single words: what it is, its element, its mood. |
| `params_json` | JSON object text with every parameter and its default, e.g. `"{\"spout\": 0.3}"`. Use `"{}"` if none. |
| `fallback_shape` | Objects only: the catalog shape that is nearest to it, used if the code fails. |
| `code` | The function body, following the spell code API. |

Leave both fields out when the library and the shapes are enough.

```json
{
  "extends": "forge_spell",
  "properties": {
    "new_object": {
      "type": "object",
      "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "params_json": {"type": "string"},
        "fallback_shape": {"type": "string", "enum": "{catalog.shapes}"},
        "code": {"type": "string"}
      },
      "required": ["id", "description", "tags", "params_json", "fallback_shape", "code"],
      "additionalProperties": false
    },
    "new_behaviors": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": {"type": "string"},
          "description": {"type": "string"},
          "tags": {"type": "array", "items": {"type": "string"}},
          "params_json": {"type": "string"},
          "code": {"type": "string"}
        },
        "required": ["id", "description", "tags", "params_json", "code"],
        "additionalProperties": false
      }
    }
  }
}
```
