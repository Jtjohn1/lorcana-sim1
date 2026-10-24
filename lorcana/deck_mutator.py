"""Deck mutation utilities, deliberately scoped to a known-safe card pool.

Per project decision: rather than opening deck construction to the full
~1,300-card database — most of which has no scripted abilities yet, so
an "optimized" deck built from it would really just be optimizing over
which cards happen to behave as no-ops least often — the mutation pool
is restricted to cards already proven out in data/decklists/deck1.txt
and deck2.txt: every one of them either uses a generic keyword the
engine fully implements, or has a real handler in card_effects.py. If
you script more decks later, add their paths to the pool build call
and the same safety property holds — the pool is only ever exactly
"cards this engine actually understands," not "all legal cards."
"""
from __future__ import annotations

import random

from .cards import Card, CardDatabase
from .decklist import parse_decklist_text


def build_card_pool(db: CardDatabase, decklist_paths: list[str]) -> list[Card]:
    """Unique cards appearing across the given decklist files."""
    seen: dict[str, Card] = {}
    for path in decklist_paths:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for _, name in parse_decklist_text(text):
            if name in seen:
                continue
            card = db.find_by_full_name(name)
            if card is None:
                raise ValueError(f"Pool card not found in database: {name}")
            seen[name] = card
    return list(seen.values())


def deck_colors(db: CardDatabase, deck_card_ids: list[int]) -> set[str]:
    return {db.get(cid).color for cid in deck_card_ids if db.get(cid).color}


def mutate_deck(
    rng: random.Random,
    db: CardDatabase,
    deck_card_ids: list[int],
    pool: list[Card],
    num_mutations: int = 1,
    max_copies: int = 4,
) -> list[int]:
    """Returns a new deck (same size) with `num_mutations` single-card swaps:
    remove one copy of a card currently in the deck, add one copy of a pool
    card. Restricted to the deck's OWN current ink colors (computed from the
    deck itself, not the whole pool), so a mutation can never produce an
    illegal 3-color deck even though the pool spans more than 2 colors
    combined across deck1/deck2."""
    deck = list(deck_card_ids)
    allowed_colors = deck_colors(db, deck)
    eligible_pool = [c for c in pool if c.color in allowed_colors]

    for _ in range(num_mutations):
        counts: dict[int, int] = {}
        for cid in deck:
            counts[cid] = counts.get(cid, 0) + 1

        remove_cid = rng.choice(deck)
        deck.remove(remove_cid)

        candidates = [c for c in eligible_pool if counts.get(c.id, 0) < max_copies]
        if not candidates:
            deck.append(remove_cid)  # no legal swap available; put it back unchanged
            continue
        add_card = rng.choice(candidates)
        deck.append(add_card.id)

    return deck
