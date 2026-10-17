// A glowing orb with a thin halo ring around it.
const g = new THREE.Group();
g.add(new THREE.Mesh(new THREE.SphereGeometry(0.32, 24, 16), api.material({ color: params.color, glow: true })));
if (params.ring) {
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.46, 0.03, 8, 48), api.material({ color: params.color, glow: true, opacity: 0.7 }));
  ring.rotation.x = Math.PI / 2.4;
  g.add(ring);
}
return g;
