# Project Status (as of GitHub `main` 4d81e76, Oct 24 2026)

## Goal
A trustworthy Disney Lorcana simulation engine: accurate gameplay, full card-effect
execution, hidden-information play, strong self-play AI, deterministic replay, statistical
deck evaluation, deck optimisation, and eventually a learned model.
Priority order: rules correctness > game-state correctness > automated tests > card effects >
search correctness > performance > learned AI > deck optimisation. The AI is downstream of
the simulator; do not expand AI work while the simulator is unreliable.

## Where the code lives
GitHub: https://github.com/Jtjohn1/lorcana-sim1 (branch `main`). Branch `codex-work` holds
Codex's original commit and is already merged into `main`.
Local project: C:\Users\Johnl\Downloads\files\lorcana_sim\lorcana_sim.
Codex also has a separate checkout under Documents\Codex\... : do not work there; use the
project folder above.

## What exists now
- Engine (`lorcana/state.py`, `engine.py`), 1,558 cards from sets 9-14 (set 14 = Hyperia City,
  `data/raw/setdata_14.json`), rebuilt reproducibly by `data/build_card_db.py`.
- Tests: 132 passing (`python -m pytest -q --ignore=lorcana/nn`): the 38 core/regression
  tests, Codex's `tests/test_engine_rules.py` (77), and tests for hidden-info defaults and for
  set 9-14 card data.
- Keywords Codex added or finished (tests exist, NOT re-audited by a human): Ward (opponent
  choices only, in `GameState.choose`), Shift variants (Duo, Combo, Temporary, Potato,
  Temporary Red Panda), Dumbo's granted ability, Underdog first-turn discount, a generic
  text-pattern resolver for simple triggers/actions/statics.
- Hidden information: `GameConfig` now defaults to HIDDEN (`open_hands = open_decks = False`).
  SearchBot samples hidden hand/deck positions (`information_samples`, default 4). The engine
  itself still never calls `view_for()`.
- Coverage audit (`lorcana/coverage.py`, `check_card_coverage.py`): 407 of 1,558 cards have a
  resolver. Decks 1-4 are fully covered; deck5 is 15/20. Decks 6-11 not yet covered.
- Deck tools: `validate_deck`, `build_supported_card_pool` (every audit-passing card; 407),
  `mutate_deck`, and `deck_evolution.py` (bounded runner that refuses unresolved seed decks).
- Overnight kit: `overnight_runner.py`, `AGENT_RULES.md`, `tasks/01-05`.

## Verified Lorcana rules (corrects earlier notes)
- Deck size: AT LEAST 60 cards, no maximum. deck4 (62) and deck5 (61) are LEGAL.
  Earlier notes calling them invalid were wrong.
- Max 4 copies per full name; max 2 ink colours.
- Dual-ink card: needs BOTH of its inks in the deck; it never adds a third colour.
  The earlier note "dual-ink counts toward either colour" was wrong. `validate_deck`
  already implements the correct rule.
- All 11 decklists pass `validate_deck` today.

## Known problems / open items
1. The audit proves a resolver EXISTS, not that it is exact. Some handlers are documented
   simplifications. Task 02 adds an exact / approximate / unhandled tier.
2. Past SearchBot-vs-HeuristicBot results were measured before hidden-info sampling. Treat
   all earlier win rates as stale. Strength is unresolved.
3. Dead `CardInstance.is_dry()` duplicate (Task 03).
4. SearchBot budget regression is unprotected by a test (Task 04). The cutoff is still global
   per root candidate once exceeded (open, not fixed).
5. `mutate_deck` silently puts a card back when no swap is possible (Task 05 documents it).
6. 1,151 of 1,558 cards have no resolver. Unscripted triggers never fire silently; unscripted
   action cards are logged no-ops. Decks 5-11 contain such cards.
7. No full trigger "bag" (player-ordered simultaneous triggers) - verify whether Codex added one
   (its code mentions a trigger queue; not audited).
8. No mulligan; no multiplayer; `view_for()` unused by the engine.
9. Encoding clean-up: `requirements.txt` in the repo is UTF-16; `README.md` ends with a UTF-16
   junk line; `.gitignore` starts with a UTF-8 BOM.
10. Docs: `README.md` was edited by Codex but not reviewed against this file.

## Next steps, in order
1. Daytime trial of the runner: `python overnight_runner.py --check`, then
   `--only 03 --hours 1`. Needs the agent CLIs installed (`claude` is not installed yet).
2. Overnight run of tasks 01-05; review both agents' branches and answer QUESTIONS.
3. Decide whether the mutation pool should exclude "approximate" cards.
4. Extend coverage to decks 5-11, one card family at a time, a test per card.
5. Only then: large SearchBot validation, deck evolution, learned AI.

## Decisions already made
- Atomic-action granularity for `legal_actions` / `apply_action`.
- `GameState.clone()` instead of deepcopy.
- Card effects are plain handler functions in registries, plus a generic text-pattern fallback.
- PyTorch unusable here; neural training paused (hand-written NumPy autodiff exists).
- Mutation pool = every card that passes the resolver audit (owner decision, implemented by
  Codex), replacing the old fixed 38-card pool.
- Owner prefers correctness-first work, one milestone at a time, a regression test per bug,
  and no silent no-ops or guessed rules.
