// Wobbles from side to side across the flight path.
const side = new THREE.Vector3().crossVectors(spell.direction, new THREE.Vector3(0, 1, 0)).normalize();
const phase = spell.rand() * Math.PI * 2;
spell.onFrame((age) => {
  if (age < 0) return;
  spell.offset.addScaledVector(side, Math.sin(age * params.rate * Math.PI * 2 + phase) * params.amount);
});
