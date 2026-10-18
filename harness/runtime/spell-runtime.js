// The spell runtime: one patch ("spell-runtime", sent by the harness's cast bridge) that every spell patch
// ("spell-<id>") uses. Built from patches/ball-slice.js: the blades on the controllers, the mouse flick on a
// ?headset desktop tab, the seeded flight that every page computes from t, and the red hit flash.
//
// A spell patch calls runtime.cast(ctx, def, code). def is the engine's spell plus t0 (the headset clock when the
// spell appears); code holds the library functions (object builder, behaviors), already checked by
// harness/codecheck. def.target says what it flies at: "user" (the head) or "egg" (ctx.egg()). Only the headset
// decides outcomes and reports them (ctx.report): sliced = "catch", reached its target = "hit", flew past =
// "shield" (a stand-in for a dodge until the shield exists).

const RULES = /*RULES*/ { deny_members: [], deny_member_pattern: '^$' };

const LAUNCH_DISTANCE = 7;   // m from the head
const EGG_LAUNCH_DISTANCE = 6; // m from the egg, on the player's side of it
const BLADE_LENGTH = 0.6;
const MIN_SWIPE_SPEED = 1.5; // m/s at the blade tip
const MIN_MOUSE_SPEED = 900; // px/s
const MAX_MESHES = 150;      // in a spell's extra visuals
const FRAME_BUDGET_MS = 4;   // for one spell's behaviors, three frames in a row
const LOOP_LIMIT = 200000;   // loop steps and calls in one spell's code per frame
const UP = new THREE.Vector3(0, 1, 0);
const FRONT = new THREE.Vector3(0, 0, -1);
// m/s² along the flight. The peak above the straight line is g·T²/8 for flight time T (about 3 s at speed 2.2):
// arc lobs about 3 m high, the others rise well under a meter.
const GRAVITY = { straight: -0.3, arc: -3, homing: -0.4, zigzag: -0.6, spiral: -0.6, boomerang: -0.6 };

// ---- guards for checked code: obj[key] arrives as obj[__k(key)], and loops and functions call __loop() ----
const DENY = new Set(RULES.deny_members);
const DENY_RE = new RegExp(RULES.deny_member_pattern);
const KEY_RE = /^([A-Za-z_$][\w$]*|\d+)$/;
function __k(key) {
  if (typeof key === 'number') return key;
  if (typeof key === 'string' && KEY_RE.test(key) && !DENY.has(key) && !DENY_RE.test(key)) return key;
  throw new Error(`property ${String(key).slice(0, 40)} is not allowed`);
}
let loopSteps = 0;
function __loop() {
  if (++loopSteps > LOOP_LIMIT) throw new Error('too many loop steps in one frame');
}

// ---- what checked code may use ----
function material({ color = '#cccccc', glow = false, opacity = 1, metalness = 0.1, roughness = 0.6 } = {}) {
  const m = new THREE.MeshStandardMaterial({ color, metalness, roughness, transparent: opacity < 1, opacity });
  if (glow) {
    m.emissive.set(color);
    m.emissiveIntensity = 0.8;
  }
  return m;
}
const api = Object.freeze({
  material,
  makeShape: (shape, mat) => ctx.makeShape(shape, mat ?? material()),
  color: (value) => new THREE.Color(value),
});

// ---- blades and mouse (from ball-slice.js) ----
const bladeGeo = new THREE.BoxGeometry(0.012, 0.012, BLADE_LENGTH).translate(0, 0, -BLADE_LENGTH / 2);
const bladeMat = new THREE.MeshBasicMaterial({ color: 0x66e0ff });
const effects = new THREE.Group(); // bursts after a slice; not in ctx.root, so viewers don't copy it
ctx.scene.add(effects);
const flash = new THREE.Mesh(
  new THREE.SphereGeometry(0.3, 16, 8),
  new THREE.MeshBasicMaterial({ color: 0xff2020, side: THREE.BackSide, transparent: true, opacity: 0, depthTest: false }),
);
flash.renderOrder = 999;
flash.visible = false;
effects.add(flash);
let flashOpacity = 0;

if (!ctx.isViewer) ctx.controllers.forEach((c) => c.add(new THREE.Mesh(bladeGeo, bladeMat)));
function addViewerBlades() {
  for (const hand of ['left', 'right']) {
    const cone = ctx.scene.getObjectByName(`viewer:hand-${hand}`);
    if (cone && !cone.children.some((o) => o.geometry === bladeGeo)) cone.add(new THREE.Mesh(bladeGeo, bladeMat));
  }
}

function seeded(k) {
  let a = (k * 2654435761) >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let x = Math.imul(a ^ (a >>> 15), 1 | a);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}
function hash(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return h >>> 0;
}

function pulse(hand, intensity, ms) {
  for (const source of ctx.renderer.xr.getSession()?.inputSources ?? []) {
    if (!hand || source.handedness === hand) source.gamepad?.hapticActuators?.[0]?.pulse?.(intensity, ms);
  }
}

const closest = new THREE.Vector3();
const segment = new THREE.Line3();
function distanceToSegment(point, a, b) {
  segment.set(a, b).closestPointToPoint(point, true, closest);
  return closest.distanceTo(point);
}

const mouse = { ndc: new THREE.Vector2(), px: new THREE.Vector2(), velocity: new THREE.Vector2(), at: 0 };
if (!ctx.isViewer) {
  ctx.on(ctx.renderer.domElement, 'pointermove', (e) => {
    const now = performance.now();
    const dt = (now - mouse.at) / 1000;
    if (dt > 0 && dt < 0.1) mouse.velocity.set(e.clientX - mouse.px.x, e.clientY - mouse.px.y).divideScalar(dt);
    mouse.px.set(e.clientX, e.clientY);
    mouse.ndc.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
    mouse.at = now;
  });
}
const raycaster = new THREE.Raycaster();

// ---- spells ----
const active = new Map(); // spell id -> state
const models = new Map(); // url -> Promise of the loaded glTF

function report(message) {
  if (!ctx.isViewer) ctx.report?.(message);
}

function codeError(state, item, err) {
  console.error(`[spell ${state.id}] ${item}:`, err);
  report({ type: 'spell-error', spell_id: state.id, item, error: String(err?.message ?? err).slice(0, 300) });
}

function countMeshes(object) {
  let n = 0;
  object.traverse((o) => { if (o.isMesh) n++; });
  return n;
}

async function buildObject(sctx, def, code, state) {
  const asset = def.asset;
  let object = null;
  if (code.object) {
    try {
      loopSteps = 0;
      object = code.object.fn(THREE, api, { ...code.object.params, color: asset.color }, __k, __loop);
      if (!object?.isObject3D) throw new Error('the builder did not return an Object3D');
      let vertices = 0;
      object.traverse((o) => { vertices += o.geometry?.attributes?.position?.count ?? 0; });
      if (vertices > 150000 || countMeshes(object) > MAX_MESHES) throw new Error('the object is too heavy');
    } catch (err) {
      codeError(state, code.object.id, err);
      object = null;
    }
  } else if (def.model) {
    try {
      if (!models.has(def.model)) models.set(def.model, sctx.load(def.model));
      const gltf = await models.get(def.model);
      const { clone } = await sctx.import('utils/SkeletonUtils.js');
      object = clone(gltf.scene);
      if (gltf.animations.length) {
        const mixer = new THREE.AnimationMixer(object);
        const clip = gltf.animations.find((a) => /run|walk|fly|dance/i.test(a.name)) ?? gltf.animations[0];
        mixer.clipAction(clip).play();
        state.mixer = mixer;
      }
    } catch (err) {
      console.error(`[spell ${state.id}] model ${def.model}:`, err);
      object = null;
    }
  }
  if (!object) return sctx.makeShape(asset.shape, material({ color: asset.color, glow: asset.glow }));
  // Center it and make its largest dimension 1 m, like the catalog shapes.
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const largest = Math.max(size.x, size.y, size.z) || 1;
  const pivot = new THREE.Group();
  object.position.sub(box.getCenter(new THREE.Vector3()));
  pivot.add(object);
  pivot.scale.setScalar(1 / largest);
  const wrapper = new THREE.Group();
  wrapper.add(pivot);
  return wrapper;
}

async function cast(sctx, def, code) {
  const id = String(def.id);
  const extras = new THREE.Group(); // world-space visuals that behaviors add
  const holder = new THREE.Group(); // unnamed: every page computes it from t, viewers don't copy the headset's
  holder.add(extras);
  sctx.scene.add(holder);
  const state = {
    id, def, t0: Number.isFinite(def.t0) ? def.t0 : null, frames: [], slowFrames: 0, done: false, frameCount: 0,
    start: new THREE.Vector3(), target: new THREE.Vector3(), velocity: new THREE.Vector3(), side: new THREE.Vector3(),
    flightTime: 2, launched: false, holder,
  };
  sctx.onRemove(() => {
    holder.removeFromParent();
    holder.traverse((o) => {
      o.geometry?.dispose();
      [o.material].flat().forEach((m) => m?.dispose());
    });
    active.delete(id);
  });

  const body = await buildObject(sctx, def, code, state);
  holder.add(body);
  state.body = body;
  state.size = def.asset.size ?? 0.35;
  state.speed = def.motion.speed ?? 2;
  state.motion = def.motion.id;
  state.channel = Math.min(2.5, Math.max(0.5, (def.channel_ms ?? 2500) / 1000));
  const random = seeded(hash(id));
  state.random = random;

  const spell = {
    object: body,
    group: extras,
    position: new THREE.Vector3(),
    direction: new THREE.Vector3(0, 0, 1),
    offset: new THREE.Vector3(),
    scale: 1,
    color: new THREE.Color(def.asset.color),
    size: state.size,
    rand: seeded(hash(id) ^ 0x9e3779b9),
    path: (age) => pathAt(state, age, new THREE.Vector3()),
    onFrame: (fn) => state.frames.push(fn),
  };
  state.spell = spell;
  state.body.visible = false; // until its first frame places it
  state.behaviorSetups = (code.behaviors ?? []).map((b) => ({ ...b, ran: false }));
  sctx.onFrame((dt, t) => update(state, dt, t));
  active.set(id, state);
}

// Where the plain flight path is at time age (seconds since launch).
function pathAt(state, age, out) {
  if (!state.launched) return out.copy(state.start);
  if (age <= 0) return out.copy(state.start);
  const T = state.flightTime;
  const p = Math.min(age / T, 1.2);
  const g = GRAVITY[state.motion] ?? -1.5;
  // v0 lands on target at T under gravity g: v0 = d/T - g*T/2 (y only).
  out.copy(state.start).addScaledVector(state.velocity, age);
  out.y += 0.5 * g * age * age - 0.5 * g * T * age;
  const envelope = Math.sin(Math.PI * Math.min(p, 1));
  if (state.motion === 'zigzag') out.addScaledVector(state.side, Math.sin(age * Math.PI * 2 * 1.2) * 0.6 * envelope);
  if (state.motion === 'spiral') {
    const a = age * Math.PI * 2 * 1.1;
    out.addScaledVector(state.side, Math.cos(a) * 0.5 * (1 - Math.min(p, 1)));
    out.addScaledVector(UP, Math.sin(a) * 0.5 * (1 - Math.min(p, 1)));
  }
  if (state.motion === 'boomerang') out.addScaledVector(state.side, 1.6 * envelope);
  return out;
}

const head = new THREE.Vector3();
const eggCenter = new THREE.Vector3();
function launch(state) {
  ctx.head.getWorldPosition(head);
  const r = state.random;
  const egg = state.def.target === 'egg' ? ctx.egg?.() : null;
  state.egg = egg;
  let distance = LAUNCH_DISTANCE;
  if (egg) {
    // It appears between the player and the egg (give or take 60°), so they can step in and slice it.
    egg.getWorldPosition(eggCenter);
    const dir = new THREE.Vector3().subVectors(head, eggCenter).setY(0);
    if (dir.lengthSq() < 0.01) dir.copy(FRONT).negate();
    dir.normalize().applyAxisAngle(UP, (r() - 0.5) * 2.1);
    state.start.copy(eggCenter).addScaledVector(dir, EGG_LAUNCH_DISTANCE);
    state.start.y = eggCenter.y + 0.5 + r() * 1.5;
    state.target.copy(eggCenter);
    distance = EGG_LAUNCH_DISTANCE;
  } else {
    const dir = FRONT.clone().applyAxisAngle(UP, (r() - 0.5) * 1.2);
    state.start.copy(head).addScaledVector(dir, LAUNCH_DISTANCE);
    state.start.y = Math.max(0.5, head.y - 0.4 + r() * 0.8);
    state.target.copy(head).add(new THREE.Vector3((r() - 0.5) * 0.3, (r() - 0.5) * 0.2, 0));
  }
  state.flightTime = Math.min(7, Math.max(1.2, distance / state.speed));
  state.velocity.subVectors(state.target, state.start).divideScalar(state.flightTime);
  state.side.crossVectors(state.velocity, UP).normalize();
  state.spell.direction.copy(state.velocity).normalize();
  state.launched = true;
}

function update(state, dt, t) {
  if (state.done) return;
  if (state.t0 === null) state.t0 = t;
  if (!state.launched) launch(state);
  const spell = state.spell;
  const age = t - state.t0 - state.channel;
  state.age = age;
  state.frameCount++;
  if (state.mixer) state.mixer.update(dt);

  // Homing: the target eases toward the head, and the path is re-aimed at it (the path moves smoothly with it).
  if (state.motion === 'homing' && !state.egg && age > 0 && age < state.flightTime) {
    ctx.head.getWorldPosition(head);
    state.target.lerp(head, Math.min(1, dt * 1.5));
    state.velocity.subVectors(state.target, state.start).divideScalar(state.flightTime);
    state.side.crossVectors(state.velocity, UP).normalize();
  }

  pathAt(state, age, spell.position);
  spell.offset.set(0, 0, 0);
  spell.scale = 1;

  // Behaviors: set up once (they may add visuals), then their per-frame functions. Any error switches them off.
  const started = performance.now();
  loopSteps = 0;
  try {
    for (const b of state.behaviorSetups) {
      if (b.ran) continue;
      b.ran = true;
      try {
        b.fn(THREE, api, spell, b.params ?? {}, __k, __loop);
      } catch (err) {
        codeError(state, b.id, err);
      }
    }
    const progress = Math.max(0, Math.min(1, age / state.flightTime));
    for (const fn of state.frames) fn(age, dt, progress);
  } catch (err) {
    codeError(state, state.behaviorSetups.map((b) => b.id).join(',') || 'behavior', err);
    state.frames = [];
  }
  const took = performance.now() - started;
  state.slowFrames = took > FRAME_BUDGET_MS ? state.slowFrames + 1 : 0;
  if (state.slowFrames >= 3) {
    codeError(state, state.behaviorSetups.map((b) => b.id).join(','), `behaviors took ${took.toFixed(1)} ms per frame`);
    state.frames = [];
  }
  if (state.frameCount % 30 === 0 && countMeshes(spell.group) > MAX_MESHES) {
    codeError(state, state.behaviorSetups.map((b) => b.id).join(','), 'too many meshes');
    state.frames = [];
    spell.group.clear();
  }

  // Place it: charging at the start (it grows in), then flying.
  const charge = age < 0 ? Math.min(1, 0.2 + 0.8 * (1 + age / state.channel)) : 1;
  state.body.position.copy(spell.position).add(spell.offset);
  state.body.scale.setScalar(state.size * spell.scale * charge);
  if (age < 0) state.body.rotation.y += dt * 2;
  if (state.frameCount === 1) {
    state.body.visible = true;
    ctx.sound?.('whoosh', state.body.position); // headset only; silent on viewers
  }
  spell.position.copy(state.body.position);

  if (ctx.isViewer || age < 0) return;
  ctx.head.getWorldPosition(head);
  if (state.egg) {
    if (state.egg.userData.box.distanceToPoint(state.body.position) < state.size * 0.5) {
      burst(state);
      finish(state, 'hit');
    } else if (age > state.flightTime + 0.6) {
      finish(state, 'shield');
    }
  } else if (state.body.position.distanceTo(head) < 0.2 + state.size * 0.5) {
    finish(state, 'hit');
    flashOpacity = 0.6;
    pulse(null, 1.0, 150);
  } else if (age > state.flightTime + 0.6) {
    finish(state, 'shield');
  }
}

function finish(state, outcome) {
  if (state.done) return;
  state.done = true;
  state.holder.visible = false;
  report({ type: 'outcome', spell_id: state.id, outcome });
  // Sliced: it shatters. Reached its target: a crack on the egg, or a thud on you. Flew past: a swish.
  if (outcome === 'catch') ctx.sound?.('shatter', state.body.position);
  else if (outcome === 'hit') ctx.sound?.(state.egg ? 'crack' : 'thud', state.egg ? state.body.position : undefined);
  else ctx.sound?.('swish', state.body.position);
}

// A burst of small pieces in the spell's color where it was sliced.
const pieceGeo = new THREE.BoxGeometry(0.05, 0.05, 0.05);
const pieces = [];
function burst(state) {
  const color = state.spell.color;
  for (let i = 0; i < 10; i++) {
    const piece = new THREE.Mesh(pieceGeo, material({ color: `#${color.getHexString()}`, glow: true }));
    piece.position.copy(state.body.position);
    piece.userData = { velocity: new THREE.Vector3().randomDirection().multiplyScalar(2 + Math.random() * 2), age: 0 };
    effects.add(piece);
    pieces.push(piece);
  }
}

function slice(state, hand) {
  burst(state);
  finish(state, 'catch');
  if (hand !== undefined) pulse(hand, 0.6, 40);
}

const blades = ctx.controllers.map((controller) => ({ controller, prevTip: new THREE.Vector3(), hasPrev: false }));
const base = new THREE.Vector3(), tip = new THREE.Vector3(), tipVelocity = new THREE.Vector3();
function checkBlades(dt) {
  for (const blade of blades) {
    const { controller } = blade;
    if (!ctx.renderer.xr.isPresenting || !controller.visible) { blade.hasPrev = false; continue; }
    controller.getWorldPosition(base);
    tip.set(0, 0, -BLADE_LENGTH).applyMatrix4(controller.matrixWorld);
    if (blade.hasPrev && dt > 0) {
      tipVelocity.subVectors(tip, blade.prevTip).divideScalar(dt);
      if (tipVelocity.length() > MIN_SWIPE_SPEED) {
        for (const state of active.values()) {
          if (state.done || !(state.age >= 0)) continue;
          const p = state.body.position;
          const radius = state.size * 0.5;
          if (distanceToSegment(p, base, tip) >= radius && distanceToSegment(p, blade.prevTip, tip) >= radius) continue;
          slice(state, controller.userData.hand);
        }
      }
    }
    blade.prevTip.copy(tip);
    blade.hasPrev = true;
  }
}
function checkMouse() {
  if (ctx.renderer.xr.isPresenting || performance.now() - mouse.at > 50 || mouse.velocity.length() < MIN_MOUSE_SPEED) return;
  raycaster.setFromCamera(mouse.ndc, ctx.camera);
  for (const state of active.values()) {
    if (state.done || !(state.age >= 0)) continue;
    if (raycaster.ray.distanceToPoint(state.body.position) > state.size * 0.5) continue;
    slice(state);
  }
}

ctx.onFrame((dt) => {
  if (ctx.isViewer) {
    addViewerBlades();
  } else {
    checkBlades(dt);
    checkMouse();
  }
  for (let i = pieces.length - 1; i >= 0; i--) {
    const piece = pieces[i];
    piece.userData.age += dt;
    piece.userData.velocity.y -= 9.8 * dt;
    piece.position.addScaledVector(piece.userData.velocity, dt);
    if (piece.userData.age > 0.8) {
      piece.removeFromParent();
      piece.material.dispose();
      pieces.splice(i, 1);
    }
  }
  flashOpacity = Math.max(0, flashOpacity - dt * 1.5);
  flash.material.opacity = flashOpacity;
  flash.visible = flashOpacity > 0;
  ctx.head.getWorldPosition(head);
  flash.position.copy(head);
});

// Spell patches find the runtime by name.
const runtime = { cast, version: 1 };
const anchor = new THREE.Object3D();
anchor.name = 'spell-runtime';
anchor.userData.runtime = runtime;
ctx.scene.add(anchor);
ctx.onRemove(() => {
  anchor.removeFromParent();
  effects.removeFromParent();
  const bladesInScene = [];
  ctx.scene.traverse((o) => { if (o.geometry === bladeGeo) bladesInScene.push(o); });
  bladesInScene.forEach((o) => o.removeFromParent());
  pieces.forEach((p) => p.material.dispose());
  [bladeGeo, bladeMat, pieceGeo, flash.geometry, flash.material].forEach((x) => x.dispose());
});
