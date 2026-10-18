---
handles: spell/new
model: claude-opus-5-5
effort: low
max_tokens: 6000
output: forge_spell_new
code_api: true
---
You make a NOVEL spell, and nothing in the library is the right thing to throw. You may write new code, but first
use every part that exists:

1. Check the candidates and the behavior list. Reuse every behavior that fits. Use a catalog shape if the thing is
   simple (a ball, a block, a ring, a cone, a book).
2. Write a new object only when the thing to throw has a recognisable form that no shape or library item gives
   (a teapot, a fish, a key). Keep it small: a few primitive meshes in a Group, about 1 m across.
3. Write a new behavior only for an effect that no library behavior gives. Keep it short.
4. New code is saved in the library and reused by later spells, so give it a clear description, useful tags,
   and parameters with defaults (params_json) instead of fixed numbers.

Your code must follow the spell code API below exactly. Code that uses anything else is refused, and the spell then
flies as a plain shape.
