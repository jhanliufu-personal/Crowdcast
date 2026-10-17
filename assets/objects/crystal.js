// A cluster of sharp crystal points growing from one base.
const g = new THREE.Group();
const material = api.material({ color: params.color, glow: true, opacity: 0.85, metalness: 0.1, roughness: 0.2 });
for (let i = 0; i < params.points; i++) {
  const point = new THREE.Mesh(new THREE.OctahedronGeometry(0.18, 0), material);
  const angle = (i / params.points) * Math.PI * 2;
  const tilt = i === 0 ? 0 : 0.5;
  point.scale.set(0.6, 2.2 - (i ? 0.6 : 0), 0.6);
  point.position.set(Math.sin(angle) * tilt * 0.3, i ? -0.05 : 0.05, Math.cos(angle) * tilt * 0.3);
  point.rotation.set(Math.cos(angle) * tilt, 0, -Math.sin(angle) * tilt);
  g.add(point);
}
return g;
