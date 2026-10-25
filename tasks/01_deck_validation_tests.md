# Task 01 - Pin and harden deck validation (Milestone 1)

ALLOW_EDIT: tests/helpers.py

## Background (verified rules - do not change these)
Core Constructed deck rules, per Ravensburger's published rules and event pages:
- A deck needs AT LEAST 60 cards. There is no maximum. (So 61- and 62-card decks are LEGAL.)
- At most 4 copies of any card with the same full name (name + version).
- At most 2 ink colours.
- A dual-ink card belongs to BOTH of its inks. It can only be included when the deck is
  playing both of those colours. It never lets a deck use a third colour.
`validate_deck(db, deck_card_ids, *, minimum_size=60, max_copies=4, max_ink_colors=2)` in
`lorcana/deck_mutator.py` already implements this and raises ValueError. All 11 decklists in
`data/decklists/` currently pass it. The previous version of this task wrongly said
deck4 (62 cards) and deck5 (61 cards) are invalid. They are legal.

## Goal
Protect the current behaviour with tests, and add an OPT-IN way to validate at load time.

## Requirements
1. Do NOT change what `validate_deck` accepts or rejects. If a test you write reveals a real
   bug, mark that test `pytest.mark.xfail(strict=True, reason=...)` and put it in QUESTIONS.
2. In `lorcana/decklist.py` add an optional `validate: bool = False` parameter to
   `load_decklist`. When True it calls `validate_deck` and lets its ValueError propagate.
   Default behaviour must stay exactly as it is now. Avoid a circular import (import inside
   the function if needed). Do NOT change `self_play.py` or any bot.
3. `tests/helpers.py`: you may add ONE optional keyword `color="Amber"` to `make_card` and
   change nothing else in that file. The existing 38+ tests must not change behaviour.

## Tests (new file `tests/test_deck_validation.py`)
Synthetic database (use `tests/helpers.py`, build many cards with distinct ids/names):
- 59 cards fails; 60 passes; 61 and 62 pass.
- 4 copies of one full name passes; 5 fails. Two different versions of the same character
  (same `name`, different `full_name`) count separately.
- One colour passes; two colours pass; three colours fail.
- Dual-ink: deck of A + B cards plus an A-B card passes. Deck of A + A-B cards passes
  (colours {A,B}). Deck of A + C cards plus an A-B card FAILS (that is three colours).
  A deck made only of A-B cards passes.
- `load_decklist(..., validate=True)` raises on an illegal list; default call does not.
Real data (`data/cards.json`, real decklists):
- decks 1 to 11 all pass `validate_deck`. Assert deck4 has 62 cards and deck5 has 61 and both pass.
- A deck made by removing 1 card from deck1 (59 cards) fails.
Do not edit any deck file.
