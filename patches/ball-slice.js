// Balls are thrown at your head from the front of the room (-Z); swipe a controller through one to slice it in half.
// Miss and your view flashes red. No gesture recognizer: each controller holds a 0.6 m blade, and a slice is the
// blade touching a ball while its tip moves faster than MIN_SWIPE_SPEED. On a ?headset desktop tab, flick the mouse
// across a ball instead.
// Like the other patches, the flight is a function of t (the headset's clock): throw k leaves at k * INTERVAL, with
// its direction and color drawn from a generator seeded with k, so viewers compute the same balls in step. Only the
// headset slices; viewers copy the halves and the hit flash from it, and hide a ball once its halves appear.
//   curl -s -X POST --data-binary @patches/ball-slice.js 'localhost:8000/api/patch?id=ball-slice'
//   curl -s -X DELETE 'localhost:8000/api/patch?id=ball-slice'

const INTERVAL = 2 / 3;      // s between throws
const FLIGHT_TIME = 1.6;     // s from launch to the head
const LAUNCH_DISTANCE = 7;   // m from the head
const BALL_GRAVITY = new THREE.Vector3(0, -3, 0);    // low, so the throw is a gentle arc
const PIECE_GRAVITY = new THREE.Vector3(0, -9.8, 0);
const PIECE_LIFE = 1.5;      // s
const RADIUS = 0.12;
const HIT_DISTANCE = RADIUS + 0.2; // ball center this close to the head counts as a hit
const BLADE_LENGTH = 0.6;
const MIN_SWIPE_SPEED = 1.5; // m/s at the blade tip
const MIN_MOUSE_SPEED = 900; // px/s
const SLOTS = 8;             // throws that can be in the air (or in pieces) at once; throw k uses slot k % SLOTS

const UP = new THREE.Vector3(0, 1, 0);
const FRONT = new THREE.Vector3(0, 0, -1);
const ballGeo = new THREE.SphereGeometry(RADIUS, 32, 16);
const domeGeo = new THREE.SphereGeometry(RADIUS, 32, 8, 0, Math.PI * 2, 0, Math.PI / 2); // top half, flat side down
const capGeo = new THREE.CircleGeometry(RADIUS, 32).rotateX(Math.PI / 2);               // faces down, closes the dome
const capMat = new THREE.MeshStandardMaterial({ color: 0xfff1d6, roughness: 0.9 });
const bladeGeo = new THREE.BoxGeometry(0.012, 0.012, BLADE_LENGTH).translate(0, 0, -BLADE_LENGTH / 2);
const bladeMat = new THREE.MeshBasicMaterial({ color: 0x66e0ff });

// The flying balls hang off their own unnamed group, not ctx.root: every page computes them from t, and viewers
// would otherwise also ease them toward the headset's last snapshot, which is ~0.1 s (half a metre) behind.
const flying = new THREE.Group();
ctx.scene.add(flying);
const slots = Array.from({ length: SLOTS }, () => {
  const ball = new THREE.Mesh(ballGeo, new THREE.MeshStandardMaterial());
  ball.visible = false;
  flying.add(ball);
  return { ball, k: -1, start: new THREE.Vector3(), velocity: new THREE.Vector3(), gone: false, piecesShown: false };
});

// ctx.root holds what viewers copy from the headset, matched by order, so keep it fixed: the flash, then two halves
// per slot (sharing the slot's ball material, so they're its color).
const flash = new THREE.Mesh(
  new THREE.SphereGeometry(0.3, 16, 8),
  new THREE.MeshBasicMaterial({ color: 0xff2020, side: THREE.BackSide, transparent: true, opacity: 0, depthTest: false }),
);
flash.renderOrder = 999;
flash.visible = false;
ctx.root.add(flash);
const pieces = Array.from({ length: SLOTS * 2 }, (_, j) => {
  const piece = new THREE.Group();
  piece.add(new THREE.Mesh(domeGeo, slots[j >> 1].ball.material), new THREE.Mesh(capGeo, capMat));
  piece.visible = false;
  piece.userData = { velocity: new THREE.Vector3(), spin: new THREE.Vector3(), age: 0 };
  ctx.root.add(piece);
  return piece;
});

ctx.onRemove(() => {
  flying.removeFromParent();
  const blades = [];
  ctx.scene.traverse((o) => { if (o.geometry === bladeGeo) blades.push(o); });
  blades.forEach((o) => o.removeFromParent());
  slots.forEach((s) => s.ball.material.dispose());
  [bladeGeo, bladeMat, ballGeo, domeGeo, capGeo, capMat].forEach((x) => x.dispose());
});

// Blades on the headset's controllers; a viewer puts them on its hand cones once those appear.
if (!ctx.isViewer) ctx.controllers.forEach((c) => c.add(new THREE.Mesh(bladeGeo, bladeMat)));
function addViewerBlades() {
  for (const hand of ['left', 'right']) {
    const cone = ctx.scene.getObjectByName(`viewer:hand-${hand}`);
    if (cone && !cone.children.some((o) => o.geometry === bladeGeo)) cone.add(new THREE.Mesh(bladeGeo, bladeMat));
  }
}

// Same numbers on every page for the same throw.
function seeded(k) {
  let a = (k * 2654435761) >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let x = Math.imul(a ^ (a >>> 15), 1 | a);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

const head = new THREE.Vector3();
const dir = new THREE.Vector3();
// Throw k: from up to ±35° either side of the room's front, aimed near where the head is now.
function launch(slot, k) {
  const random = seeded(k);
  dir.copy(FRONT).applyAxisAngle(UP, (random() - 0.5) * 1.2);
  slot.start.copy(head).addScaledVector(dir, LAUNCH_DISTANCE);
  slot.start.y = Math.max(0.3, head.y - 0.6 + random() * 1.2);
  const target = head.clone().add(new THREE.Vector3((random() - 0.5) * 0.3, (random() - 0.5) * 0.2, 0));
  // Launch velocity that lands on `target` after FLIGHT_TIME under BALL_GRAVITY: v0 = d/T - g*T/2.
  slot.velocity.subVectors(target, slot.start).divideScalar(FLIGHT_TIME).addScaledVector(BALL_GRAVITY, -FLIGHT_TIME / 2);
  slot.ball.material.color.setHSL(random(), 0.75, 0.5);
  slot.k = k;
  slot.gone = false;
}

let sliced = 0, hits = 0, flashOpacity = 0;

function pulse(hand, intensity, ms) {
  for (const source of ctx.renderer.xr.getSession()?.inputSources ?? []) {
    if (!hand || source.handedness === hand) source.gamepad?.hapticActuators?.[0]?.pulse?.(intensity, ms);
  }
}

// Split the slot's ball along the plane with this normal; the halves fly apart along it.
function slice(slot, normal) {
  const i = slots.indexOf(slot);
  for (const [n, side] of [[0, 1], [1, -1]]) {
    const away = normal.clone().multiplyScalar(side);
    const piece = pieces[i * 2 + n];
    piece.position.copy(slot.ball.position);
    piece.quaternion.setFromUnitVectors(UP, away); // dome points away from the cut
    piece.userData.velocity.copy(slot.velocity).multiplyScalar(0.4).addScaledVector(away, 1.5);
    piece.userData.spin.randomDirection().multiplyScalar(6);
    piece.userData.age = 0;
    piece.visible = true;
  }
  slot.gone = true;
  slot.ball.visible = false;
  sliced++;
}

const closest = new THREE.Vector3();
const segment = new THREE.Line3();
function distanceToSegment(point, a, b) {
  segment.set(a, b).closestPointToPoint(point, true, closest);
  return closest.distanceTo(point);
}

const blades = ctx.controllers.map((controller) => ({ controller, prevTip: new THREE.Vector3(), hasPrev: false }));
const base = new THREE.Vector3(), tip = new THREE.Vector3(), tipVelocity = new THREE.Vector3(), bladeDir = new THREE.Vector3();
function checkBlades(dt) {
  for (const blade of blades) {
    const { controller } = blade;
    if (!ctx.renderer.xr.isPresenting || !controller.visible) { blade.hasPrev = false; continue; }
    controller.getWorldPosition(base);
    tip.set(0, 0, -BLADE_LENGTH).applyMatrix4(controller.matrixWorld);
    if (blade.hasPrev && dt > 0) {
      tipVelocity.subVectors(tip, blade.prevTip).divideScalar(dt);
      if (tipVelocity.length() > MIN_SWIPE_SPEED) {
        for (const slot of slots) {
          if (!slot.ball.visible) continue;
          // The blade now, or the path its tip swept since the last frame, passes through the ball.
          const p = slot.ball.position;
          if (distanceToSegment(p, base, tip) >= RADIUS && distanceToSegment(p, blade.prevTip, tip) >= RADIUS) continue;
          bladeDir.subVectors(tip, base).normalize();
          const normal = new THREE.Vector3().crossVectors(bladeDir, tipVelocity).normalize();
          slice(slot, normal.lengthSq() > 0 ? normal : new THREE.Vector3(1, 0, 0));
          pulse(controller.userData.hand, 0.6, 40);
        }
      }
    }
    blade.prevTip.copy(tip);
    blade.hasPrev = true;
  }
}

// Desktop (?headset): a fast mouse flick whose ray crosses a ball slices it.
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
function checkMouse() {
  if (ctx.renderer.xr.isPresenting || performance.now() - mouse.at > 50 || mouse.velocity.length() < MIN_MOUSE_SPEED) return;
  raycaster.setFromCamera(mouse.ndc, ctx.camera);
  for (const slot of slots) {
    if (!slot.ball.visible || raycaster.ray.distanceToPoint(slot.ball.position) > RADIUS) continue;
    const right = new THREE.Vector3().setFromMatrixColumn(ctx.camera.matrixWorld, 0);
    const up = new THREE.Vector3().setFromMatrixColumn(ctx.camera.matrixWorld, 1);
    const motion = right.multiplyScalar(mouse.velocity.x).addScaledVector(up, -mouse.velocity.y);
    slice(slot, new THREE.Vector3().crossVectors(raycaster.ray.direction, motion).normalize());
  }
}

ctx.onFrame((dt, t) => {
  ctx.head.getWorldPosition(head);

  // The balls: the same on every page.
  for (const slot of slots) slot.ball.visible = false;
  const latest = Math.floor(t / INTERVAL);
  for (let k = Math.max(0, latest - SLOTS + 1); k <= latest; k++) {
    const slot = slots[k % SLOTS];
    if (slot.k !== k) launch(slot, k);
    const age = t - k * INTERVAL;
    if (slot.gone || age > FLIGHT_TIME + 1) continue; // sliced, hit you, or dodged
    slot.ball.position.copy(slot.start).addScaledVector(slot.velocity, age).addScaledVector(BALL_GRAVITY, 0.5 * age * age);
    slot.ball.visible = true;
    if (slot.ball.position.distanceTo(head) < HIT_DISTANCE) {
      slot.gone = true;
      slot.ball.visible = false;
      if (!ctx.isViewer) {
        hits++;
        flashOpacity = 0.6;
        pulse(null, 1.0, 150);
        console.log(`[ball-slice] hit! sliced ${sliced}, hits ${hits}`);
      }
    }
  }

  if (ctx.isViewer) {
    // The headset's halves and flash arrive by themselves (after this). When a slot's halves first show up, the
    // headset has sliced that ball: hide it, and start the halves where it is rather than gliding from their last spot.
    addViewerBlades();
    slots.forEach((slot, i) => {
      const shown = pieces[i * 2].visible;
      if (shown && !slot.piecesShown) {
        slot.gone = true;
        slot.ball.visible = false;
        pieces[i * 2].position.copy(slot.ball.position);
        pieces[i * 2 + 1].position.copy(slot.ball.position);
      }
      slot.piecesShown = shown;
    });
    flash.material.opacity = 0.35;
    return;
  }

  checkBlades(dt);
  checkMouse();
  for (const piece of pieces) {
    if (!piece.visible) continue;
    const { velocity, spin } = piece.userData;
    velocity.addScaledVector(PIECE_GRAVITY, dt);
    piece.position.addScaledVector(velocity, dt);
    piece.rotation.x += spin.x * dt;
    piece.rotation.y += spin.y * dt;
    piece.rotation.z += spin.z * dt;
    piece.userData.age += dt;
    if (piece.userData.age > PIECE_LIFE) piece.visible = false;
  }
  flashOpacity = Math.max(0, flashOpacity - dt * 1.5);
  flash.material.opacity = flashOpacity;
  flash.visible = flashOpacity > 0;
  flash.position.copy(head);
});
