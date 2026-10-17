// Spins the thrown object around one of its axes.
spell.onFrame((age, dt) => {
  spell.object.rotation[params.axis] += params.speed * dt;
});
