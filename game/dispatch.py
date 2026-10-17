"""Seam between the game engine and the LLM scene agent, for REMIX and NOVEL requests.

Not implemented yet. A real dispatcher would:
  - run up to some number of LLM calls at once (has_capacity() says whether another fits),
  - REMIX: ask for a small, local edit of the referenced catalog blocks (a new motion for an
    existing asset, a recolor or resize, two blocks combined), keeping everything else as is;
  - NOVEL: ask for a whole new spell (asset built from prefabs, motion, name, incantation);
  - validate the result and call done(request, spell, None) with a dict shaped like
    spell_request.resolve_preset()'s, or done(request, None, error_message);
  - call done from any thread; the engine takes its own lock.
"""


class NotConnectedDispatcher:
    """Default until the LLM side exists: no capacity, so REMIX and NOVEL requests wait in the queue."""

    online = False

    def has_capacity(self):
        return False

    def submit(self, request, catalog, done):
        raise NotImplementedError("LLM dispatch isn't implemented yet")
