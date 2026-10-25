# Task 04 - Regression test for the SearchBot node-budget bug (audit 5.3)

ALLOW_EDIT:

## Background
Audit bug 5.3 (fixed, unprotected): the node budget used to be shared across sibling
top-level candidates, so the first candidate used it all and the rest were never really
searched. The fix resets `self._nodes_visited = 0` before each root action in
`SearchBot.choose` (`bots/search_bot.py`). SearchBot now also samples hidden positions
(`information_samples`, default 4) when `open_hands` / `open_decks` is False, and
`max_total_nodes` is a strict cap on the whole decision. A depth is accepted only if every
root action finished in every sample.

## Goal
TEST ONLY: change no production code.

## Requirements
- Read `bots/search_bot.py` fully first.
- New file `tests/test_search_bot.py`, small synthetic state from `tests/helpers.py` with
  several top-level candidates (for example several playable characters in hand with enough
  ink). Use a perfect-information config (`open_hands = open_decks = True`) so there is one
  sample and results are deterministic; seed every RNG.
- Show that EVERY root candidate was searched below the root, not just statically evaluated.
  Do this with a subclass or wrapper that records calls to `_minimax` (depth argument and
  `_nodes_visited` at entry), not by editing production code.
- Second test: with `max_total_nodes` set small, `_total_nodes_visited` never exceeds it and
  `choose` still returns a legal action.
- Third test: with hidden information on (default config) and `information_samples=2`, the
  trace (`verbose=True`, `trace_log`) reports `information_samples == 2`.
- Mutation check (mandatory): temporarily remove or move the `self._nodes_visited = 0`
  reset so the budget is shared, confirm the first test FAILS, restore the file exactly.
  Report this in DONE. If the test cannot be made to fail, do not commit it; explain in
  QUESTIONS.
- Fast (a couple of seconds) and deterministic. Do not touch the separate open issue
  (cutoff is global per root candidate once exceeded); you may mention it in QUESTIONS.
