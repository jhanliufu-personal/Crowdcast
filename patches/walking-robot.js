// A robot that walks a loop on the ground floor, ahead of where you start. Come within 2.5 m and it stops,
// turns to face you and waves; walk away and it carries on.
//   curl -s -X POST --data-binary @patches/walking-robot.js 'localhost:8000/api/patch?id=walking-robot'
// Model: RobotExpressive by Tomás Laulhé (quaternius), modified by Don McCurdy, CC0 1.0 (from the three.js examples).
const gltf = await ctx.load('https://cdn.jsdelivr.net/gh/mrdoob/three.js@r170/examples/models/gltf/RobotExpressive/RobotExpressive.glb');
const robot = gltf.scene;
robot.scale.setScalar(1.4 / new THREE.Box3().setFromObject(robot).getSize(new THREE.Vector3()).y); // 1.4 m tall
ctx.root.add(robot);

// Animation clips: Idle, Walking, Running, Dance, Wave, Jump, Yes, No, ThumbsUp, Punch, Death, Sitting, Standing.
const mixer = new THREE.AnimationMixer(robot);
const actions = Object.fromEntries(gltf.animations.map((clip) => [clip.name, mixer.clipAction(clip)]));
actions.Wave.setLoop(THREE.LoopOnce, 1);
actions.Wave.clampWhenFinished = true;
let current = actions.Walking.play();
function fadeTo(name) {
  const next = actions[name];
  if (next === current) return;
  next.reset().fadeIn(0.3).play();
  current.fadeOut(0.3);
  current = next;
}
ctx.on(mixer, 'finished', () => fadeTo('Idle')); // after waving

const waypoints = [[-2.5, -3.5], [2.5, -3.5], [2.5, -6.5], [-2.5, -6.5]].map(([x, z]) => new THREE.Vector3(x, 0, z));
const SPEED = 0.9; // m/s
let next = 1;
let state = 'walk';
robot.position.copy(waypoints[0]);
const user = new THREE.Vector3();
const toTarget = new THREE.Vector3();

ctx.onFrame((dt) => {
  mixer.update(dt);
  ctx.camera.getWorldPosition(user);
  user.y = 0;
  const userDistance = user.distanceTo(robot.position);
  if (state === 'walk' && userDistance < 2.5) {
    state = 'greet';
    fadeTo('Wave');
  } else if (state === 'greet' && userDistance > 3.5) {
    state = 'walk';
    fadeTo('Walking');
  }

  toTarget.subVectors(state === 'greet' ? user : waypoints[next], robot.position);
  const distance = toTarget.length();
  // Turn smoothly toward the target, the short way round (the model faces +Z), keeping the angle within ±π.
  const wrap = (angle) => THREE.MathUtils.euclideanModulo(angle + Math.PI, Math.PI * 2) - Math.PI;
  const turn = wrap(Math.atan2(toTarget.x, toTarget.z) - robot.rotation.y);
  robot.rotation.y = wrap(robot.rotation.y + turn * Math.min(1, dt * 5));
  if (state === 'walk') {
    if (distance < 0.2) next = (next + 1) % waypoints.length;
    else robot.position.addScaledVector(toTarget.divideScalar(distance), Math.min(SPEED * dt, distance));
  }
});
