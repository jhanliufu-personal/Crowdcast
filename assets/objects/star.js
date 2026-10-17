// A thick star with params.points points.
const shape = new THREE.Shape();
for (let i = 0; i <= params.points * 2; i++) {
  const r = i % 2 ? 0.2 : 0.5;
  const a = (i / (params.points * 2)) * Math.PI * 2;
  if (i === 0) shape.moveTo(Math.sin(a) * r, Math.cos(a) * r);
  else shape.lineTo(Math.sin(a) * r, Math.cos(a) * r);
}
const geometry = new THREE.ExtrudeGeometry(shape, { depth: params.depth, bevelEnabled: false });
geometry.center();
const g = new THREE.Group();
g.add(new THREE.Mesh(geometry, api.material({ color: params.color, glow: true })));
return g;
