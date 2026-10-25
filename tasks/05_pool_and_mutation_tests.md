# Task 05 - Tests for the audit-driven mutation pool

ALLOW_EDIT:

## Background
`build_supported_card_pool(db)` in `lorcana/deck_mutator.py` returns every card that passes
the coverage audit (407 cards across sets 9-14 at last count). `mutate_deck` swaps cards
within the deck's current ink colours, enforces copy limits by full name, and calls
`validate_deck` before and after. When no swap is possible it silently puts the removed card
back. The owner wants the pool to be "every card that passes the resolver audit".

## Requirements
TEST ONLY: change no production code. Write the tests the current code should satisfy.

## Tests (new file `tests/test_mutation_pool.py`)
- Real data: pool is non-empty, has no duplicate ids, contains cards from every set 9-14,
  and every card in it has an empty `card_effect_gaps`.
- Real data: for a sample of 30 cards that have a gap, none is in the pool.
- Real data: a card of a dual-ink colour (for example colour "Amber-Steel") is only eligible
  for decks that already contain BOTH colours.
- Starting from deck1 (and again from deck2) with a seeded `random.Random`, apply
  `mutate_deck` 200 times in a loop (feeding each result back in). After every step the deck
  has the same size, passes `validate_deck`, and has at most 4 copies per full name.
- Same seed gives the same mutation sequence (determinism).
- Silent put-back: build a case where no legal swap exists (a pool of one card already at
  4 copies, or an empty pool) and show the deck is returned unchanged. Mark this test
  `pytest.mark.xfail(strict=True, reason="mutate_deck silently ignores impossible swaps; should be visible")`
  asserting that the caller can tell no swap happened, and put it in QUESTIONS.
