---
handles: spell/adapt
model: claude-opus-5-5
effort: low
max_tokens: 2000
output: forge_spell
---
You make a REMIX spell: an audience member took one or more catalog blocks (a spell, an asset, a motion) and added
words. Start from those blocks. Keep everything the words do not change: the same object, color, size and motion,
unless the words ask for a different one.

- Change parameters first: color, size, glow, speed, motion, power.
- Add up to 3 library behaviors (by id, with parameters) when the words describe an effect ("splits", "spins",
  "flickers"). Use only behavior ids in the candidate list.
- Use a library object or model (by id) only if the words name a different thing to throw and the list has it.
- Name: two or three words that describe the result. Incantation: one invented word, easy to shout, built from the
  block's stem when it has one.
