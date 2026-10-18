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
| `target` | `"egg"` or `"user"`: what it flies at. The egg is the thing the player guards; the user is the player in the headset, and a hit costs them health. Choose what the request asks for. If it does not say, choose what fits: heavy, slow or destructive spells at the egg; quick, sneaky or mischievous spells at the user. |
| `behaviors` | 0-3 library behaviors: `{"id": "...", "params_json": "{...}"}`. Use `"{}"` for the defaults. |
| `sound` | The sound it makes when it appears, played in the headset: `base` (`whoosh` thrown, `buzz` insects or engines, `chime` bells and magic, `rumble` heavy and earthy, `zap` electric, `boing` springy or silly, `honk` comic animals and horns, `sizzle` fire and fizz), `pitch` (0.5 deep and big to 2 tiny and squeaky; 1 normal), `wobble` (0 steady to 1 warbling), `sparkle` (true adds twinkles, for magic), `length` (0.3-1.5 s). Match the thing and its mood. |

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
    "target": {"type": "string", "enum": ["egg", "user"]},
    "behaviors": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {"id": {"type": "string"}, "params_json": {"type": "string"}},
        "required": ["id", "params_json"],
        "additionalProperties": false
      }
    },
    "sound": {
      "type": "object",
      "properties": {
        "base": {"type": "string", "enum": ["whoosh", "buzz", "chime", "rumble", "zap", "boing", "honk", "sizzle"]},
        "pitch": {"type": "number"},
        "wobble": {"type": "number"},
        "sparkle": {"type": "boolean"},
        "length": {"type": "number"}
      },
      "required": ["base", "pitch", "wobble", "sparkle", "length"],
      "additionalProperties": false
    }
  },
  "required": ["name", "incantation", "object", "color", "size", "glow", "motion", "speed", "power", "target", "behaviors", "sound"],
  "additionalProperties": false
}
```
