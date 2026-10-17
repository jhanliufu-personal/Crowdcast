// Splits into params.count copies that spread apart after params.at of the flight.
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
    copy.visible = spread > 0 && spell.object.visible;
    copy.position.copy(spell.position);
    copy.position.x += Math.cos(angle) * spread;
    copy.position.y += Math.sin(angle) * spread;
    copy.quaternion.copy(spell.object.quaternion);
    copy.scale.copy(spell.object.scale);
  });
});
