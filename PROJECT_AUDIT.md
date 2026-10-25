# Lorcana Simulator - Audit (state of `main` 4d81e76)

This replaces the Oct 4 audit, which described code that has since changed a lot.

## Audit scope (honest)
Run directly: full test suite (132 pass), card-data rebuild (reproduces `cards.json`), coverage
report on decks 1-5, `validate_deck` on all 11 decklists, pool size and composition.
Read in full or in part: `lorcana/coverage.py`, `lorcana/deck_mutator.py`,
`lorcana/decklist.py`, `bots/search_bot.py` (`choose`, `__init__`), `GameState.choose`,
`bots/targeting.py` (head), `deck_evolution.py` (head), the runner and task files.
NOT audited: the ~1,360 changed lines of `lorcana/engine.py`, `lorcana/card_effects.py`,
`bots/heuristic_bot.py`, most of `tests/test_engine_rules.py`, `lorcana/nn/*`.
Claims about those come from Codex's summary and passing tests, not from a fresh read.

## 1. Rules facts that were wrong before (now verified)
- Deck size is a minimum of 60, not exactly 60. deck4 (62) and deck5 (61) are legal.
- Dual-ink cards need both inks in the deck. A deck cannot use a third colour via a dual card.
  Verified by experiment: an Amber + Emerald deck with two Amber-Steel cards correctly fails
  `validate_deck` (three colours).

## 2. What is working (evidence)
- 132 tests pass on a fresh clone of GitHub `main`.
- Set 9-14 data present and consistent: 1,558 cards; `build_card_db.py` output is identical to
  the committed file; tests fail if set 14 raw data or compiled cards are removed.
- Ward blocks opponents' chosen targets in `GameState.choose` (code read; test exists by name).
- Shift variants (Duo, Combo, Temporary, Potato, Temporary Red Panda) have tests by name.
- `validate_deck` correct on all 11 real decks; dual-ink handled by union of colours.

## 3. Risks, ranked
1. **Audit means "a resolver exists", not "correct".** 407 supported cards include documented
   approximations. Deck evolution results are only as good as those handlers.
2. **Old SearchBot results are stale.** Hidden-info sampling changed the bot; its strength and
   the earlier "loses ~70/30 to HeuristicBot" figure need re-measurement.
3. **1,151 cards unscripted.** Unscripted triggers are invisible; decks 5-11 contain them.
4. **Engine rewrite not independently reviewed.** Large diff, tests written by the same agent
   that wrote the code. The overnight tasks add independent tests; a human should review.
5. **`GameConfig` default flipped to hidden.** README and any old script that assumed open
   information may now behave differently.
6. **Silent no-ops remain** in `mutate_deck` (impossible swap) and for unscripted cards.
7. SearchBot node cutoff global per root candidate (open). Budget fix (5.3) has no test.
8. Dead `CardInstance.is_dry()`.
9. File encoding problems (UTF-16 requirements.txt, README tail, .gitignore BOM).
10. No mulligan; no multiplayer; `view_for()` not used by the engine.

## 4. Search assumptions (current code)
- Depth counts atomic actions. `max_nodes` resets before each root action; `max_total_nodes`
  caps the whole decision; a depth counts only if every root action finished in every sample.
- Root values are averaged over `information_samples` sampled hidden positions.
- Sub-decisions inside simulated play are answered by the searching bot's own heuristic for
  both players, not by branching.

## 5. Decklist validity
All 11 decklists pass `validate_deck`. Sizes: deck4 = 62, deck5 = 61, others 60.

## 6. Coverage snapshot
407/1,558 cards (26%). Decks 1-4: 100% of unique cards. Deck 5: 15/20; missing examples:
Aladdin & Genie - Mischievous Pals, John Silver - Alien Pirate, Lyle Tiberius Rourke,
Retro Evolution Device, Taran - Magically Armed.

## 7. Recommended order
1. Overnight tasks 01-05 (see `tasks/`).
2. Review branches; merge only what passes review.
3. Decide on excluding approximate cards from the pool.
4. Extend coverage to decks 5-11; re-measure SearchBot; then deck evolution.
