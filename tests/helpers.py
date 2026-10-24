"""Shared fixtures for the engine tests.

All tests use a tiny synthetic card database (built in code), so they do not
depend on data/cards.json and do not break when real card data changes.
"""
from __future__ import annotations

from lorcana import engine
from lorcana.cards import Ability, Card, CardDatabase
from lorcana.state import GameConfig

FILLER_ID = 1  # cost-1 inkable vanilla character used to pad decks and hands


def make_card(card_id, name=None, type="Character", cost=1, strength=1,
              willpower=2, lore=1, inkwell=True, keywords=(), subtypes=(),
              abilities=(), effect_text=""):
    name = name or f"Test{card_id}"
    is_char = type == "Character"
    return Card(
        id=card_id, set_number=0, set_name="test", number=card_id,
        name=name, version=None, full_name=name, type=type, color="Amber",
        cost=cost, inkwell=inkwell,
        strength=strength if is_char else None,
        willpower=willpower if is_char else None,
        lore=lore if is_char else None,
        move_cost=None, subtypes=tuple(subtypes),
        keyword_abilities=tuple(keywords), abilities=tuple(abilities),
        full_text=effect_text, rarity=None, story=None,
    )


def kw_ability(keyword, number=None):
    return Ability(type="keyword", name=None, keyword=keyword,
                   keyword_value=None, keyword_value_number=number,
                   costs=(), effect=None, full_text=keyword)


def make_db(*cards):
    db = CardDatabase.__new__(CardDatabase)  # skip loading cards.json
    db.sets = []
    db.by_id, db.by_name = {}, {}
    for c in (make_card(FILLER_ID, "Filler"),) + tuple(cards):
        db.by_id[c.id] = c
        db.by_name.setdefault(c.name, []).append(c)
    return db


def make_state(*cards, deck_size=20, seed=0):
    """Fresh game on the main phase, player 0 active, turn 1.

    new_game() picks a random first player and always draws 7, so we force
    active_idx = 0 afterwards. Decks are 20 filler cards so multi-end_turn
    tests don't deck out. Both hands start with 7 filler cards.
    """
    db = make_db(*cards)
    deck = [FILLER_ID] * deck_size
    state = engine.new_game(db, list(deck), list(deck), GameConfig(), seed=seed)
    state.active_idx = 0
    state.turn_number = 1
    state.phase = "main"
    for p in state.players:
        p.inked_this_turn = False
    return state


def put(state, pidx, card_id, zone, dry=True, exerted=False):
    """Create a new instance of card_id directly in a zone; return its iid."""
    inst = state.new_instance(card_id, pidx, zone)
    getattr(state.players[pidx], zone).append(inst.instance_id)
    if zone == "play":
        inst.entered_play_turn = state.turn_number - 1 if dry else state.turn_number
        inst.exerted = exerted
    return inst.instance_id


def add_ready_ink(state, pidx, n):
    for _ in range(n):
        put(state, pidx, FILLER_ID, "inkwell")


def find(state, kind, **match):
    return [a for a in engine.legal_actions(state)
            if a["kind"] == kind and all(a.get(k) == v for k, v in match.items())]
