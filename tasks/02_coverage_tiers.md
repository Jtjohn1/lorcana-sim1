# Task 02 - Add an "approximate" tier to the coverage audit (Milestone 2)

ALLOW_EDIT:

## Background
`lorcana/coverage.py` decides whether a card is "supported" by checking that a resolver
EXISTS (a registered handler or a generic text pattern). It does not know whether the
resolver matches the printed text exactly. Today 407 of 1,558 cards pass. Some handlers are
known simplifications (the older README listed Hades - Looking for a Deal, Aurora - Holding
Court, Mrs. Incredible - Super Stretchy, Cheshire Cat; Codex's newer README and code comments
may list more). The mutation pool and deck evolution currently trust every card that passes.

## Goal
Make "exact" vs "approximate" visible, without changing any default behaviour.

## Requirements
- Add `APPROXIMATE_CARDS: dict[str, str]` to `lorcana/coverage.py` mapping a card
  `full_name` to a one-line reason. Fill it ONLY from explicit evidence in the repo: README
  sections about interpretation calls, and code comments in `lorcana/card_effects.py` /
  `lorcana/engine.py` containing words like "simplif", "approx", "interpret", "assum",
  "not modeled", "not implemented". List every entry you added, with the evidence line, in
  DONE. Never add an entry from your own opinion about a card.
- Add `card_effect_tier(card) -> "exact" | "approximate" | "unhandled"`.
  unhandled = `card_effect_gaps` is non-empty. approximate = no gaps but the card is in
  `APPROXIMATE_CARDS`. exact = everything else.
- `is_card_effect_supported` must keep returning exactly what it returns today.
  `build_supported_card_pool` in `lorcana/deck_mutator.py` gets a keyword
  `include_approximate: bool = True`; default behaviour unchanged. (The owner decides later
  whether to turn it off.)
- `check_card_coverage.py`: print exact / approximate / unhandled counts per set and overall,
  and for each `--deck` list its approximate cards. Must not crash on any of the 11 decklists.
- Never guess. If you cannot tell whether a handler is exact, leave the card as exact and
  say so in QUESTIONS.

## Tests (new file `tests/test_coverage_tiers.py`)
- Synthetic cards (see how `tests/test_engine_rules.py` builds cards and registers
  handlers; register and unregister inside the test so registries are restored): one with no
  gaps, one with a gap, one in `APPROXIMATE_CARDS`.
- `include_approximate=False` removes exactly the approximate cards from the pool and
  nothing else; the default pool is identical in size to today's (assert against a value
  computed from `is_card_effect_supported`, not a hard-coded 407).
- Real data: every card in the default pool has `card_effect_tier != "unhandled"`.
- Real data: decks 1 to 11 run through the tier report without error.
