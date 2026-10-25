# Task 03 - Delete dead CardInstance.is_dry (Milestone 3)

ALLOW_EDIT: tests/test_regressions.py
ALLOWED_TEST_DROP: 16

## Background
`CardInstance.is_dry(controller_turns_started)` in `lorcana/state.py` duplicates
`GameState.is_dry(instance_id)`. Every engine/bot call uses `GameState.is_dry`.
`tests/test_regressions.py` has a parametrized test (16 cases) that only compares the two.

## Requirements
1. Prove it is dead: search the whole repo (lorcana/, bots/, self_play.py,
   deck_evolution.py, check_card_coverage.py, tests/) for every `.is_dry(` and show in DONE
   that each call is `GameState.is_dry`.
2. If, and only if, nothing calls `CardInstance.is_dry`, delete that method and nothing else.
   If something does call it, change nothing and explain in QUESTIONS.
3. In `tests/test_regressions.py`, delete the `test_dead_is_dry_agrees_with_live_one` test
   (and its decorators) because it tests the deleted method. Change nothing else in that file.
   The runner allows the passing-test count to drop by 16 for this task only.

## Tests (new file `tests/test_state_api.py`)
- `CardInstance` has no `is_dry`; `GameState` still has it.
- A character put into play this turn is not dry; one put in play on an earlier turn is dry
  (use `tests/helpers.py`; note dryness now depends on the owner's `turns_started`, so read
  `GameState.is_dry` first and set up state accordingly).
- Prove the test can fail: temporarily re-add the method, confirm the first test fails,
  remove it again. Report this in DONE.
