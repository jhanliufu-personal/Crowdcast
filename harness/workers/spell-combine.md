---
handles: spell/combine
model: claude-haiku-5-5
effort: low
max_tokens: 2000
output: forge_spell
---
You make a NOVEL spell from parts that already exist. The audience member wrote a description; the library candidates
below matched words of it. Build the spell only from these parts:

- The thing to throw: a library object or model id from the candidates, or a catalog shape ("shape:sphere").
  If a candidate is a whole spell, you may copy its object, behaviors and parameters and change only what the words
  ask for.
- Its look: color, size, glow.
- Its flight: one catalog motion and a speed.
- Its effects: up to 3 library behaviors, by id, with parameters.

Do not invent ids. If no candidate is the right thing to throw, use the nearest catalog shape and a matching color.
