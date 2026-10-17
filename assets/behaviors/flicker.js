// Flickers in and out of sight, like a flame or a faulty light.
const phase = spell.rand() * 10;
spell.onFrame((age) => {
  const wave = Math.sin((age + phase) * params.rate * Math.PI * 2) + Math.sin((age + phase) * params.rate * 4.7);
  spell.object.visible = wave > -2 + params.visible * 4;
});
