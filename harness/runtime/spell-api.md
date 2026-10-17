# Spell code API

New library code is the **body of a function**. Do not write the function header. Use only the names below, plain
JavaScript (const, let, for loops with a fixed count, arrow functions, arrays, objects, Math), and your own variables.
`window`, `document`, `fetch`, `this`, `while`, classes, `async`, imports, and any loader or renderer are refused.
Property names such as `parent`, `constructor` and `scene` are refused too. Keep each body under 4000 characters.

## Object builder

`function (THREE, api, params) { <your code> }`. Build the thing to throw and **return** it: a `THREE.Group` (or a
Mesh), about **1 m** across and centered on the origin, facing -Z. The runtime scales it to the spell's size, colors
it if you use `api.material`, and moves it. `params` holds the item's `params_json` defaults, overridden by the
spell's `object_params_json`, plus `params.color` (a `#rrggbb` string).

```js
const g = new THREE.Group();
const body = new THREE.Mesh(new THREE.SphereGeometry(0.35, 24, 16), api.material({ color: params.color }));
body.scale.set(1, 0.8, 1);
g.add(body);
const spout = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.07, 0.4, 12), api.material({ color: params.color }));
spout.position.set(0.4, 0.05, 0);
spout.rotation.z = -Math.PI / 3;
g.add(spout);
return g;
```

## Behavior

`function (THREE, api, spell, params) { <your code> }`. Runs once when the spell appears. Register per-frame logic
with `spell.onFrame`. Everything must depend only on `age`, `progress` and `spell.rand()`, so every screen shows the
same thing.

| Name | What it is |
|---|---|
| `spell.object` | The thrown Object3D. You may change its rotation, its scale (through `spell.scale`), its materials and its children. |
| `spell.group` | A Group in world coordinates for extra visuals (copies, sparks, trails). It is cleaned up with the spell. |
| `spell.position` | Where the spell is this frame (a Vector3, read only). |
| `spell.direction` | Unit vector of the flight direction (read only). |
| `spell.offset` | A Vector3 added to the flight path this frame. It is reset to (0, 0, 0) before each frame. |
| `spell.scale` | Number multiplied with the size this frame. Reset to 1 before each frame. |
| `spell.color` | The spell's `THREE.Color`. |
| `spell.size` | The spell's size in meters. |
| `spell.rand()` | Random number in [0, 1), the same on every screen. |
| `spell.path(age)` | Where the plain flight path is at time `age` (a new Vector3). |
| `spell.onFrame(fn)` | Calls `fn(age, dt, progress)` every frame. `age` is seconds since launch (negative while the spell charges up), `progress` goes from 0 at launch to 1 at the target. |

| Helper | What it does |
|---|---|
| `api.material({ color, glow, opacity, metalness, roughness })` | A standard material. `glow: true` makes it emissive. |
| `api.makeShape(shape, material)` | A catalog shape (`box`, `sphere`, `cylinder`, `cone`, `torus`, `book`), 1 m across. |
| `api.color(value)` | A `THREE.Color`. |

```js
// Split into params.count copies that spread apart after params.at of the flight.
const copies = [];
for (let i = 1; i < params.count; i++) {
  const copy = spell.object.clone();
  copy.visible = false;
  spell.group.add(copy);
  copies.push(copy);
}
spell.onFrame((age, dt, progress) => {
  const spread = Math.max(0, progress - params.at) * params.spread * 2;
  copies.forEach((copy, i) => {
    const angle = ((i + 1) / params.count) * Math.PI * 2;
    copy.visible = spread > 0;
    copy.position.copy(spell.position);
    copy.position.x += Math.cos(angle) * spread;
    copy.position.y += Math.sin(angle) * spread;
    copy.scale.copy(spell.object.scale);
  });
});
```

Limits, checked while the spell flies: at most 150 meshes in `spell.group`, about 4 ms of work per frame, and no
error. A behavior that breaks a limit is switched off; the spell continues as a plain flight.
