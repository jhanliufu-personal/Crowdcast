// A giant, furious eyeball hovering in the room. It glares at your head wherever you go, trembles with rage,
// throbs red light over the room, lunges at you every few seconds, and its slit pupil narrows as you get closer.

// Bloodshot sclera, drawn once onto a canvas. On the sphere's UVs the iris (+Z) sits at canvas (256, 256),
// so veins start out wide around it and wiggle inward, thinning as they approach the iris.
function bloodshotTexture() {
  const c = document.createElement('canvas');
  c.width = 1024;
  c.height = 512;
  const g = c.getContext('2d');
  g.fillStyle = '#f3e4da';
  g.fillRect(0, 0, c.width, c.height);
  const flush = g.createRadialGradient(256, 256, 80, 256, 256, 420);
  flush.addColorStop(0, 'rgba(200, 30, 30, 0)');
  flush.addColorStop(1, 'rgba(200, 30, 30, 0.55)');
  g.fillStyle = flush;
  g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = '#a50f18';
  g.lineCap = 'round';
  function vein(x, y, angle, width, steps) {
    for (let i = 0; i < steps; i++) {
      const nx = x + Math.cos(angle) * 6, ny = y + Math.sin(angle) * 6;
      g.lineWidth = width;
      g.beginPath();
      g.moveTo(x, y);
      g.lineTo(nx, ny);
      g.stroke();
      x = nx; y = ny;
      angle += (Math.random() - 0.5) * 0.6;
      width *= 0.96;
      if (Math.random() < 0.08 && width > 0.8) vein(x, y, angle + (Math.random() - 0.5) * 1.5, width * 0.7, steps - i);
    }
  }
  for (let i = 0; i < 45; i++) {
    const a = Math.random() * Math.PI * 2;
    vein(256 + Math.cos(a) * 270, 256 + Math.sin(a) * 270, a + Math.PI, 4.5, 30);
  }
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

const eye = new THREE.Group();
eye.name = 'eye';
const sclera = new THREE.MeshStandardMaterial({ map: bloodshotTexture(), emissive: 0x500000, roughness: 0.2 });
eye.add(new THREE.Mesh(new THREE.SphereGeometry(0.35, 48, 32), sclera));
// Iris and pupil are squashed spheres on the +Z side, which is the side lookAt() points at the target.
const iris = new THREE.Mesh(new THREE.SphereGeometry(0.14, 32, 16), new THREE.MeshStandardMaterial({ color: 0xd01010, emissive: 0xff2000, roughness: 0.3 }));
iris.scale.z = 0.35;
iris.position.z = 0.31;
const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.065, 24, 12), new THREE.MeshBasicMaterial({ color: 0x000000 }));
pupil.position.z = 0.345;
// A heavy upper lid: a cap over the top of the ball, tipped forward so it hangs low over the iris in a glare.
const lid = new THREE.Mesh(new THREE.SphereGeometry(0.36, 48, 16, 0, Math.PI * 2, 0, 1.05), new THREE.MeshStandardMaterial({ color: 0x5a1414, roughness: 0.6 }));
lid.rotation.x = 0.3;
// Brows in a furious V, inner ends low.
const browMat = new THREE.MeshStandardMaterial({ color: 0x1a0505, roughness: 0.9 });
const brows = [-1, 1].map((side) => {
  const brow = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.07, 0.08), browMat);
  brow.position.set(side * 0.15, 0.37, 0.2);
  brow.rotation.z = side * 0.4;
  return brow;
});
const rageLight = new THREE.PointLight(0xff1a00, 3, 5);
eye.add(iris, pupil, lid, ...brows, rageLight);
ctx.root.add(eye);

const head = new THREE.Vector3();
const home = new THREE.Vector3();
const toHead = new THREE.Vector3();
const aim = new THREE.Object3D();
ctx.onFrame((dt, t) => {
  ctx.head.getWorldPosition(head);
  home.set(0, 2.1 + Math.sin(t * 0.9) * 0.08, -1.4);
  toHead.subVectors(head, home);
  const dist = toHead.length();
  // Every 6 s, lunge toward your face and snap back, never closer than 0.8 m.
  const phase = t % 6;
  const lunge = phase < 0.6 ? Math.sin((phase / 0.6) * Math.PI) * Math.min(0.6, Math.max(0, dist - 0.8)) : 0;
  eye.position.copy(home).addScaledVector(toHead.normalize(), lunge);
  // Trembling with rage.
  eye.position.x += (Math.random() - 0.5) * 0.015;
  eye.position.y += (Math.random() - 0.5) * 0.015;
  aim.position.copy(eye.position);
  aim.lookAt(head);
  eye.quaternion.slerp(aim.quaternion, Math.min(1, dt * 10));
  const blink = t % 4.3; // a 0.15 s squash every 4.3 s
  eye.scale.y = blink < 0.15 ? 0.1 + 0.9 * Math.abs(blink - 0.075) / 0.075 : 1;
  // Slit pupil narrows as you approach.
  const close = THREE.MathUtils.clamp((2.5 - dist) / 1.5, 0, 1);
  pupil.scale.set(0.45 - 0.3 * close, 1.3, 0.4);
  // Throbbing red glow, faster and brighter mid-lunge; brows twitch.
  const throb = 0.5 + 0.5 * Math.sin(t * (lunge > 0 ? 18 : 6));
  iris.material.emissiveIntensity = 0.6 + 1.4 * throb;
  sclera.emissiveIntensity = 0.3 + 0.7 * throb;
  rageLight.intensity = 2 + 6 * throb;
  brows.forEach((brow, i) => { brow.rotation.z = (i ? 1 : -1) * (0.4 + 0.08 * Math.sin(t * 23 + i)); });
});
