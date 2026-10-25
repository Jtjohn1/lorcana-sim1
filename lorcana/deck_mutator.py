"""Deck mutation utilities, deliberately scoped to a known-safe card pool.

Only cards whose modeled keywords and structured abilities all have an
engine resolver enter the default mutation pool. This prevents deck
evolution from rewarding cards whose printed effects silently do nothing.
"""
from __future__ import annotations

import random

from .cards import Card, CardDatabase
from .coverage import is_card_effect_supported
from .decklist import parse_decklist_text


def build_supported_card_pool(db: CardDatabase) -> list[Card]:
    """All database cards whose parsed text has a registered resolver."""
    return sorted(
        (card for card in db.all_cards() if is_card_effect_supported(card)),
        key=lambda card: card.id,
    )


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
            if is_card_effect_supported(card):
                seen[name] = card
    return list(seen.values())


def card_colors(card: Card) -> set[str]:
    """Return each ink color represented by a mono- or dual-ink card."""
    return set(card.color.split("-")) if card.color else set()


def deck_colors(db: CardDatabase, deck_card_ids: list[int]) -> set[str]:
    colors: set[str] = set()
    for cid in deck_card_ids:
        colors.update(card_colors(db.get(cid)))
    return colors


def validate_deck(
    db: CardDatabase,
    deck_card_ids: list[int],
    *,
    minimum_size: int = 60,
    max_copies: int = 4,
    max_ink_colors: int = 2,
) -> None:
    """Raise ValueError if a deck violates core constructed deck limits."""
    if len(deck_card_ids) < minimum_size:
        raise ValueError(f"Deck has {len(deck_card_ids)} cards; minimum is {minimum_size}")
    colors = deck_colors(db, deck_card_ids)
    if len(colors) > max_ink_colors:
        raise ValueError(f"Deck has {len(colors)} ink colors; maximum is {max_ink_colors}")
    counts: dict[str, int] = {}
    for cid in deck_card_ids:
        card = db.get(cid)
        counts[card.full_name] = counts.get(card.full_name, 0) + 1
        if counts[card.full_name] > max_copies:
            raise ValueError(f"Deck has more than {max_copies} copies of {card.full_name}")


def mutate_deck(
    rng: random.Random,
    db: CardDatabase,
    deck_card_ids: list[int],
    pool: list[Card],
    num_mutations: int = 1,
    max_copies: int = 4,
) -> list[int]:
    """Return a same-size legal deck after up to `num_mutations` card swaps.

    Candidates are restricted to the deck's current ink colors. The source
    deck is checked first, and copy counts are enforced by full card name.
    """
    deck = list(deck_card_ids)
    validate_deck(db, deck, minimum_size=0, max_copies=max_copies)
    if not deck:
        return deck
    allowed_colors = deck_colors(db, deck)
    eligible_pool = [card for card in pool if card_colors(card) <= allowed_colors]

    for _ in range(num_mutations):
        remove_cid = rng.choice(deck)
        deck.remove(remove_cid)
        counts: dict[str, int] = {}
        for cid in deck:
            card_name = db.get(cid).full_name
            counts[card_name] = counts.get(card_name, 0) + 1

        candidates = [card for card in eligible_pool if counts.get(card.full_name, 0) < max_copies]
        if not candidates:
            deck.append(remove_cid)  # no legal swap available; put it back unchanged
            continue
        add_card = rng.choice(candidates)
        deck.append(add_card.id)

    validate_deck(db, deck, minimum_size=0, max_copies=max_copies)
    return deck
