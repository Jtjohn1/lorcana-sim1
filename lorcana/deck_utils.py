"""Deck construction helpers.

Nothing here is part of the core rules engine — it's just enough to get
a legal 60-card, <=2-ink-type, <=4-copies deck for self-play testing
before any real deckbuilding/optimization layer exists (see the project
README's roadmap step 6, "deck optimization").
"""
from __future__ import annotations

import random

from .cards import Card, CardDatabase

INK_COLORS = ("Amber", "Amethyst", "Emerald", "Ruby", "Sapphire", "Steel")


def build_random_deck(
    db: CardDatabase,
    rng: random.Random,
    colors: tuple[str, str] | None = None,
    size: int = 60,
    max_copies: int = 4,
) -> list[int]:
    """Returns a list of card ids (with duplicates) forming a legal deck.

    Picks two ink colors (or uses the ones given), then fills the deck by
    randomly drawing eligible cards up to `max_copies` copies each, ignoring
    deckbuilding strategy entirely (no curve-shaping, no synergy) — this is
    strictly a legality-satisfying random deck for exercising the engine.
    """
    if colors is None:
        colors = tuple(rng.sample(INK_COLORS, 2))

    pool = [c for c in db.all_cards() if c.color in colors]
    rng.shuffle(pool)

    counts: dict[str, int] = {}
    deck: list[int] = []
    i = 0
    # Cycle through the shuffled pool, adding copies until the deck is full.
    while len(deck) < size:
        if i >= len(pool):
            i = 0
            if not any(counts[c.full_name] < max_copies for c in pool if c.full_name in counts) and len(set(c.full_name for c in pool)) * max_copies <= len(deck):
                break  # pool exhausted at max copies; avoid infinite loop
        card = pool[i]
        i += 1
        n = counts.get(card.full_name, 0)
        if n >= max_copies:
            continue
        counts[card.full_name] = n + 1
        deck.append(card.id)

    return deck[:size]
