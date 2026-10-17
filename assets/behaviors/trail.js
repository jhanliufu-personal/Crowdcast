// Leaves a trail of small fading glowing balls behind it along the flight path.
const balls = [];
for (let i = 0; i < params.count; i++) {
  const ball = api.makeShape('sphere', api.material({ color: spell.color, glow: true, opacity: 1 - i / params.count }));
  ball.visible = false;
  spell.group.add(ball);
  balls.push(ball);
}
spell.onFrame((age) => {
  balls.forEach((ball, i) => {
    const t = age - (i + 1) * params.gap;
    ball.visible = t > 0;
    if (!ball.visible) return;
    ball.position.copy(spell.path(t));
    ball.scale.setScalar(spell.size * params.size * (1 - i / params.count));
  });
});
