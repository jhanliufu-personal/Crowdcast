// Grows (or shrinks, with to < 1) during the flight, reaching params.to times its size at the target.
spell.onFrame((age, dt, progress) => {
  spell.scale *= 1 + (params.to - 1) * Math.max(0, progress);
});
