"""Shared policies for decisions made while card effects are resolving."""
from __future__ import annotations


def card_value(state, iid: int) -> float:
    """Estimate a card's hand value from its printed stats and abilities."""
    card = state.card_of(iid)
    value = 1.25 * card.cost + (card.strength or 0)
    value += 0.5 * (card.willpower or 0) + 2.0 * (card.lore or 0)
    value += 1.5 * len(card.keyword_abilities)
    value += 2.0 * sum(ability.type != "keyword" for ability in card.abilities)
    return value


def choose_discard(state, options: list[int]) -> int:
    """Discard the least promising card by its printed stats and abilities."""
    return min(options, key=lambda iid: card_value(state, iid))


def choose_play_from_discard(state, options: list[int]) -> int | None:
    """Use free-play effects on the strongest character currently available."""
    return max(options, key=lambda iid: card_value(state, iid)) if options else None


def choose_opponent_discard(state, options: list[int]) -> int | None:
    """When selecting an opponent's discard, remove their strongest option."""
    return max(options, key=lambda iid: card_value(state, iid)) if options else None


def choose_look_and_take(state, options: list[int | None]) -> int | None:
    """Take the strongest matching card revealed from the top of a deck."""
    cards = [iid for iid in options if iid is not None]
    return max(cards, key=lambda iid: card_value(state, iid)) if cards else None


def choose_damage_target(state, owner: int, amount: int, options: list[int]) -> int | None:
    from lorcana.engine import _damage_after_resist

    opponent = state.opponent_idx(owner)

    def value(iid: int) -> float:
        card = state.card_of(iid)
        inst = state.instances[iid]
        remaining = max((card.willpower or 0) - inst.damage, 0)
        dealt = _damage_after_resist(state, iid, amount)
        return (
            (1000 if inst.owner == opponent else -1000)
            + (100 if dealt >= remaining else 0)
            + 4 * (card.cost or 0) + 2 * (card.lore or 0)
            + (card.strength or 0) + 2 * dealt
            - max(dealt - remaining, 0)
        )

    return max(options, key=value) if options else None


def choose_exert_target(state, owner: int, options: list[int]) -> int | None:
    opponent = state.opponent_idx(owner)
    def value(iid: int) -> float:
        card = state.card_of(iid)
        return (
            (1000 if state.instances[iid].owner == opponent else -1000)
            + (0 if state.instances[iid].exerted else 100)
            + 4 * (card.lore or 0) + 2 * (card.strength or 0)
            + (card.cost or 0)
        )
    return max(options, key=value) if options else None


def choose_banish_target(state, options: list[int]) -> int | None:
    def value(iid: int) -> float:
        card = state.card_of(iid)
        return 4 * (card.cost or 0) + 3 * (card.lore or 0) + (card.strength or 0)
    return max(options, key=value) if options else None


def choose_bounce_target(state, owner: int, options: list[int]) -> int | None:
    opponent = state.opponent_idx(owner)
    def value(iid: int) -> float:
        card = state.card_of(iid)
        return (1000 if state.instances[iid].owner == opponent else 0) + card_value(state, iid)
    return max(options, key=value) if options else None


def choose_heal_target(state, owner: int, options: list[int]) -> int | None:
    def value(iid: int) -> float:
        card = state.card_of(iid)
        inst = state.instances[iid]
        healed = min(inst.damage, 99)
        return (1000 if inst.owner == owner else -1000) + 4 * healed + (card.cost or 0)
    return max(options, key=value) if options else None


def choose_opponent_item_to_banish(state, owner: int, options: list[int]) -> int | None:
    """Prefer removing an opponent item, with expensive items ahead of cheap ones."""
    def value(iid: int) -> float:
        card = state.card_of(iid)
        enemy = state.instances[iid].owner != owner
        return (1000 if enemy else -1000) + 5 * (card.cost or 0) + card_value(state, iid)
    return max(options, key=value) if options else None


def choose_buff_target(state, owner: int, options: list[int | None]) -> int | None:
    """Give a challenge and damage-resistance buff to a useful friendly character."""
    candidates = [iid for iid in options if iid is not None]
    if not candidates:
        return None
    def value(iid: int) -> float:
        card = state.card_of(iid)
        friendly = state.instances[iid].owner == owner
        return (1000 if friendly else -1000) + 5 * (card.lore or 0) + 2 * (card.strength or 0) + card.cost
    return max(candidates, key=value)


def choose_rapunzel_discard(state, owner: int, options: list[int | None]) -> int | None:
    """Discard only when the resulting bounce can remove an opposing character."""
    if not any(iid is not None for iid in options):
        return None
    enemy_targets = [
        iid for iid in state.characters_in_play(state.opponent_idx(owner))
        if not state.card_of(iid).has_keyword("Ward")
    ]
    if not enemy_targets:
        return None
    cards = [iid for iid in options if iid is not None]
    return choose_discard(state, cards) if cards else None
