# Task 01 - Deck-legality validation (Milestone 1)

ALLOW_EDIT:

## Goal
`lorcana/decklist.py` currently only checks that card names resolve. Add real
legality validation, without changing what `load_decklist` does by default.

## Requirements
- Add `validate_deck(db, card_ids)` that returns a list of human-readable problem
  strings (empty list = legal). Do not raise; do not print.
- Checks:
  1. Deck size is exactly 60 by default. Make it a parameter (e.g. `deck_size=60`)
     because the owner's notes say 60 but the exact official wording (exactly 60 vs
     minimum 60) has not been verified. If the Comprehensive Rules text is in the repo,
     read it and report in QUESTIONS what it says; otherwise say you could not verify.
  2. At most 4 copies of any card with the same card name + version (use the repo's
     existing notion of a card's identity; read cards.py to see what `full_name` is).
  3. At most 2 ink colours. Dual-ink cards (e.g. "Amethyst-Emerald") count toward
     EITHER of their colours, they are not a third colour. The deck is legal if some
     choice of at most two colours covers every card (each card has at least one of its
     colours in the chosen set). Inspect how colour is stored on `Card` before coding.
- Add an opt-in `validate=False` parameter to `load_decklist` that raises `ValueError`
  listing the problems when True. Default behaviour must stay exactly as it is now.
  Do NOT change `self_play.py` or any bot.
- Do not edit any deck file.

## Tests (new file `tests/test_decklist.py`)
- Synthetic-database tests (use `tests/helpers.py` style fixtures) for: 59/60/61
  cards, 4 vs 5 copies, 2 colours legal, 3 colours illegal, dual-ink cards counted
  towards either colour (a deck of A, A/B, B/C... construct cases that are legal only
  because of dual-ink handling, and cases that are illegal even with it).
- Real-data tests using `data/cards.json` and the real decklists: deck1 passes;
  deck4 (62 cards) and deck5 (61 cards) fail with a size problem. Also assert that
  decks 2,3,6,7,8,9,10,11 pass. If one of those unexpectedly fails, do NOT change the
  test or the deck: use `xfail(strict=True)` with the reason and report it in QUESTIONS.
