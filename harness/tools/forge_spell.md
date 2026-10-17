---
name: forge_spell
kind: output
---
Your answer is one JSON object, the spell:

| Field | Meaning |
|---|---|
| `name` | 2-3 words, at most 40 characters. |
| `incantation` | One invented word, 2-20 letters, easy to shout. Not "Aegis", and not one of the incantations in use. |
| `flavor` | Optional. One short line for the narrator. |
| `object` | What is thrown: `"shape:<shape>"` with a catalog shape, or the id of a library object or model. |
| `object_params_json` | Optional. JSON object text with parameters for a library object, e.g. `"{\"petals\": 6}"`. Leave out what you don't change. |
| `color` | `#rrggbb`. |
| `size` | Meters, 0.1-1.2. Most spells are 0.25-0.5. |
| `glow` | true for fire, magic and light; false for solid things. |
| `motion` | One catalog motion id. |
| `speed` | Meters per second, 0.8-4. Faster is harder to catch. |
| `power` | 1-3: charge when caught. More complex and rarer spells are worth more. |
| `behaviors` | 0-3 library behaviors: `{"id": "...", "params_json": "{...}"}`. Use `"{}"` for the defaults. |

```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "incantation": {"type": "string"},
    "flavor": {"type": "string"},
    "object": {"type": "string"},
    "object_params_json": {"type": "string"},
    "color": {"type": "string"},
    "size": {"type": "number"},
    "glow": {"type": "boolean"},
    "motion": {"type": "string", "enum": "{catalog.motions}"},
    "speed": {"type": "number"},
    "power": {"type": "integer"},
    "behaviors": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "params_json": {"type": "string"}},
        "required": ["id", "params_json"],
        "additionalProperties": false
      }
    }
  },
  "required": ["name", "incantation", "object", "color", "size", "glow", "motion", "speed", "power", "behaviors"],
  "additionalProperties": false
}
```
