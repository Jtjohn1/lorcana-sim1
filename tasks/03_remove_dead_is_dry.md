# Task 03 - Delete dead CardInstance.is_dry (Milestone 3)

ALLOW_EDIT:

## Goal
`CardInstance.is_dry()` in `lorcana/state.py` is a dead duplicate of
`GameState.is_dry`; it has an unused parameter and is a trap for future edits.

## Requirements
- First prove it is dead: search the whole repo (including `bots/`, `lorcana/nn/`,
  `self_play.py`, tests) for every use of `.is_dry(` and confirm each call goes to
  `GameState.is_dry`. List what you found in DONE.
- If, and only if, nothing calls `CardInstance.is_dry`, delete that method and
  nothing else. If something does call it, change nothing and explain in QUESTIONS.
- `tests/test_regressions.py` already skips its dead-`is_dry` test once the method is
  gone. Do not edit that file.

## Tests
- Add `tests/test_state_api.py` with one test asserting `CardInstance` no longer has
  `is_dry` and `GameState` still does, and one test that a character put into play
  this turn is not dry while one put in play on an earlier turn is (use
  `tests/helpers.py`, `put(..., dry=True/False)`).
- Full suite must still pass.
