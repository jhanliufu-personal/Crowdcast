# Tags

One section for each tag. The lines at the start of a section are settings; the rest is text that the worker gets
when a request has the tag.

| Setting | Meaning |
|---|---|
| `applies_to` | `spell`, `scene` or `both`. |
| `weight` | Scene requests only: added to the priority (default 0). |
| `sources` | Who may use the tag (default: everyone). A tag from any other source is ignored. |

Spells get their tags from the catalog blocks in the recipe (`shared/catalog.json`). Scene requests get them from
the sender (`POST /api/scene/requests {"text", "tags"}`). Unknown tags are ignored and logged.

## fire
applies_to: spell
Fire: warm colors (#ff3000 to #ffd000), glow on, quick and direct motions (straight, zigzag). Good behaviors: flicker, trail, pulse.

## ice
applies_to: spell
Ice: pale blues and white (#9fe3ff, #e8fbff), glow on, sharp shapes (cone), homing or spiral motions. Good behaviors: spin, trail.

## lightning
applies_to: spell
Lightning: bright yellow or white (#ffe14d, #ffffff), glow on, fast (speed 3-4), zigzag motion. Good behaviors: flicker, split.

## plant
applies_to: spell
Plant: greens and browns (#3fae4a, #6b4f2a), no glow, slower (speed 1.2-2), spiral or arc motions. Good behaviors: grow, wobble.

## heavy
applies_to: spell
Heavy: solid colors, no glow, slow (speed 1-1.8), arc (lob) motion, power 2-3. Good behaviors: spin.

## object
applies_to: spell
Everyday object: keep its real colors, no glow unless the words say it is magic. Good behaviors: spin, wobble.

## elemental
applies_to: spell
Elemental: the element sets the color; glow on.

## urgent
applies_to: scene
weight: 100
sources: [chat, host, api]
This request is urgent: do exactly what it says with the fewest commands.

## cosmetic
applies_to: scene
weight: -10
This request is cosmetic: change only looks (color, size, glow, animation), not positions.

## atmosphere
applies_to: scene
weight: 5
This request is about the mood of the room: prefer glowing objects and slow animations (float, orbit).
