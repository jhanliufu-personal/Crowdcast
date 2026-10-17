# Scene commands

The scene editor answers with a list of these commands (the `edit_scene` answer format). One section for each
command: what it does, then its fields as a JSON schema.

## add_object
Add a new object to the scene. Coordinates are meters: x right, y up (floor is y=0), z toward the starting view
(-z is forward). Use a short unique, human-readable name, e.g. `blue-sphere-1`. Size is the largest dimension in
meters (0.05-2). Animations: `float` bobs gently in place, `spin` turns in place, `orbit` circles about 1 m around
the position. `glow` makes it light up its surroundings.

```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "shape": {"type": "string", "enum": ["box", "sphere", "cylinder", "cone", "torus", "book"]},
    "color": {"type": "string"},
    "size": {"type": "number"},
    "position": {"type": "array", "items": {"type": "number"}},
    "animation": {"type": "string", "enum": ["none", "float", "spin", "orbit"]},
    "glow": {"type": "boolean"}
  },
  "required": ["name", "shape", "color", "size", "position", "animation"],
  "additionalProperties": false
}
```

## update_object
Change an existing object, by its name. Include only the fields that change. The name and shape can't change.

```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "color": {"type": "string"},
    "size": {"type": "number"},
    "position": {"type": "array", "items": {"type": "number"}},
    "animation": {"type": "string", "enum": ["none", "float", "spin", "orbit"]},
    "glow": {"type": "boolean"}
  },
  "required": ["name"],
  "additionalProperties": false
}
```

## remove_object
Remove an object from the scene, by its name. Use the name `*` to remove every object you can edit.

```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"}
  },
  "required": ["name"],
  "additionalProperties": false
}
```
