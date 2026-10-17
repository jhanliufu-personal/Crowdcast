// Invisible for most of the flight; appears only when params.reveal of the way to the target is done.
spell.onFrame((age, dt, progress) => {
  spell.object.visible = age < 0 || progress > params.reveal;
});
