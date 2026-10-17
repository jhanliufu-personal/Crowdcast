// A jagged lightning bolt made of params.segments thin bars.
const g = new THREE.Group();
const material = api.material({ color: params.color, glow: true });
let x = 0, y = 0.5;
for (let i = 0; i < params.segments; i++) {
  const nx = i % 2 ? 0.12 : -0.12;
  const ny = 0.5 - ((i + 1) / params.segments);
  const dx = nx - x, dy = ny - y;
  const bar = new THREE.Mesh(new THREE.BoxGeometry(0.06, Math.hypot(dx, dy), 0.06), material);
  bar.position.set((x + nx) / 2, (y + ny) / 2, 0);
  bar.rotation.z = -Math.atan2(dx, dy);
  g.add(bar);
  x = nx;
  y = ny;
}
return g;
