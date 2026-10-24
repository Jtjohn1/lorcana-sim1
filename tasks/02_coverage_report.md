# Task 02 - Per-deck scripted-coverage report (Milestone 2)

ALLOW_EDIT:

## Goal
Today an unscripted card is invisible: unscripted triggered abilities never fire (no
log), unscripted action cards are logged no-ops, unscripted activated abilities are
never offered. Nothing tells the owner how much of a given deck is affected. Build a
read-only report.

## Requirements
- New module `lorcana/coverage.py`. Read `lorcana/engine.py` and `lorcana/cards.py`
  first to learn the registries (triggered handlers keyed by (full_name, event),
  action-effect handlers, activated-ability handlers, static registries) and how
  `Card.abilities` marks keyword vs triggered vs activated vs static.
- `analyze_card(card)` and `analyze_deck(db, card_ids)` return plain data (dicts or
  dataclasses) classifying every card into: fully handled (vanilla / keywords only /
  all its non-keyword abilities have handlers) or not, and listing, per unhandled
  card, exactly which abilities are unhandled and what the engine does today
  (trigger never fires / action card no-op / activated ability never offered /
  static effect ignored).
- Never guess: if you cannot tell how a given ability type is handled from the code,
  classify it as "unknown" and list it in QUESTIONS rather than assuming.
- A command line entry: `python -m lorcana.coverage data/decklists/deck3.txt`
  prints a readable summary (counts, then the unhandled cards with ability text).
  Must not crash on any of the 11 decklists, including invalid deck4/deck5.
- No changes to engine behaviour. Read-only.

## Tests (new file `tests/test_coverage.py`)
- Synthetic cards: a vanilla card, a keyword-only card, a card with an unhandled
  triggered ability, an unhandled action card, an unhandled activated ability, and
  a card with a registered handler (register and unregister it inside the test so
  global registries are restored).
- Real data: deck1 and deck2 report zero unhandled cards (the README says they are
  fully scripted). If that is false, do not weaken the test: record the true
  current result in an `xfail(strict=True)` test and report it in QUESTIONS.
- Real data: decks 3-11 run through `analyze_deck` without error.
