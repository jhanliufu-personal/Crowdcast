// Throbs: the size swells and shrinks params.rate times a second.
spell.onFrame((age) => {
  spell.scale *= 1 + params.amount * Math.sin(age * params.rate * Math.PI * 2);
});
