"""Conservative audit of whether the engine has a resolver for card text."""
from __future__ import annotations

import re

from . import engine
from .cards import Card


SUPPORTED_KEYWORDS = {
    "Adventurous", "Alert", "Bodyguard", "Boost", "Challenger", "Evasive",
    "Reckless", "Resist", "Rush", "Shift", "Singer", "Sing Together", "Support", "Ward",
}
TRIGGER_PATTERNS = (
    ("on_play", r"when you play this character"),
    ("on_play_item", r"when you play this item"),
    ("on_play_location", r"when you play this location"),
    ("on_play_action", r"(?:once during your turn, )?whenever you play an action"),
    ("on_quest", r"whenever this character quests"),
    ("on_banish_in_challenge", r"when this character is banished in a challenge"),
    ("on_banish", r"when this character is banished(?! in a challenge)"),
    ("on_being_challenged", r"whenever this character is challenged"),
    ("on_challenge", r"whenever this character challenges"),
    ("on_ready", r"whenever this character is readied"),
    ("on_start_of_turn", r"at the start of your turn"),
    ("on_end_of_turn", r"at the end of your turn"),
    ("on_ally_quest", r"whenever one of your other characters quests"),
    ("on_ally_challenge", r"whenever one of your other characters challenges"),
    ("on_move", r"whenever this character moves"),
    ("on_character_moves_here", r"whenever a character moves here"),
    ("on_ink_drop_removed", r"whenever you remove an ink drop"),
    ("on_boost", r"whenever this character is boosted"),
    ("on_boost", r"whenever you put a card under this character"),
)


def _known_keyword(keyword: str | None) -> bool:
    if not keyword:
        return False
    if keyword in SUPPORTED_KEYWORDS:
        return True
    if keyword.endswith(" Shift"):
        return True
    return False


def _trigger_events(effect: str | None) -> list[str]:
    if not effect:
        return []
    text = " ".join(effect.lower().split())
    return [
        event for event, pattern in TRIGGER_PATTERNS
        if re.search(pattern, text)
    ]


def card_effect_gaps(card: Card) -> list[str]:
    """Return ability descriptions that lack a known engine resolver.

    This is intentionally conservative: a false gap only removes a card
    from trusted deck evolution; a false pass could reward a no-op effect.
    """
    gaps: list[str] = []
    for ability in card.abilities:
        if ability.type == "keyword":
            if not _known_keyword(ability.keyword):
                gaps.append(f"unsupported keyword: {ability.keyword or ability.name}")
        elif ability.type == "triggered":
            events = _trigger_events(ability.effect)
            if not events:
                gaps.append(f"unrecognized trigger: {ability.effect or ability.full_text}")
                continue
            for event in events:
                registered = (
                    (card.full_name, event) in engine.TRIGGERED_ABILITY_HANDLERS
                    or (card.name, event) in engine.TRIGGERED_ABILITY_HANDLERS
                )
                generic = engine._simple_generic_trigger_effect(ability, event) is not None
                if not registered and not generic:
                    gaps.append(f"{event}: {ability.effect or ability.full_text}")
        elif ability.type == "activated":
            costs_supported = engine._parse_activated_costs(ability.costs) is not None
            registered = ability.name in engine.ACTIVATED_ABILITY_HANDLERS
            generic = engine._simple_generic_activated_effect(ability) is not None
            if not costs_supported or not (registered or generic):
                gaps.append(f"activated ability: {ability.effect or ability.full_text}")
        elif ability.type == "static":
            registered = any((
                card.full_name in engine.STATIC_READY_BLOCKERS,
                card.name in engine.STATIC_READY_BLOCKERS,
                card.full_name in engine.STATIC_LORE_MODIFIERS,
                card.name in engine.STATIC_LORE_MODIFIERS,
                card.full_name in engine.STATIC_DAMAGE_SOURCE_OVERRIDE,
                card.name in engine.STATIC_DAMAGE_SOURCE_OVERRIDE,
                card.full_name in engine.STATIC_TURN_START_DISCOUNT,
                card.name in engine.STATIC_TURN_START_DISCOUNT,
                card.full_name in engine.STATIC_GRANTED_ACTIVATED_ABILITIES,
                card.name in engine.STATIC_GRANTED_ACTIVATED_ABILITIES,
                card.full_name in engine.STATIC_CARD_RESOLVERS,
                card.name in engine.STATIC_CARD_RESOLVERS,
            ))
            enter_exerted = bool(
                ability.effect and "enter play exerted" in ability.effect.lower()
            )
            song_rule = None
            if ability.effect and card.type == "Action" and "Song" in card.subtypes:
                song_rule = re.fullmatch(
                    r"a character with cost (\d+) or more can ⟳ to sing this song for free",
                    " ".join(ability.effect.lower().split()).rstrip(" ."),
                )
            generic_song = bool(song_rule and int(song_rule.group(1)) == card.cost)
            generic_static = any((
                engine._simple_generic_static_strength_effect(ability) is not None,
                engine._simple_generic_static_resist_effect(ability) is not None,
                engine._simple_generic_static_ready_threshold(ability) is not None,
                engine._simple_generic_named_play_discount(ability) is not None,
            ))
            underdog_discount = bool(
                ability.name == "UNDERDOG" and ability.effect and re.fullmatch(
                    r"if this is your first turn and you're not the first player, "
                    r"you pay \d+ ⬡ less to play this character",
                    " ".join(ability.effect.lower().split()).rstrip(" ."),
                )
            )
            if not registered and not enter_exerted and not generic_song and not underdog_discount and not generic_static:
                gaps.append(f"static ability: {ability.effect or ability.full_text}")
        else:
            gaps.append(f"unknown ability type: {ability.type}")

    if card.type == "Action" and card.full_text.strip():
        handled = (
            card.full_name in engine.ACTION_EFFECT_HANDLERS
            or card.name in engine.ACTION_EFFECT_HANDLERS
            or engine._generic_action_plan(card) is not None
        )
        if not handled:
            gaps.append(f"action effect: {card.full_text}")

    return gaps


def is_card_effect_supported(card: Card) -> bool:
    return not card_effect_gaps(card)
def deck_effect_gaps(db, deck_card_ids: list[int]) -> dict[str, list[str]]:
    """Return unresolved effects for each unique card in a deck."""
    cards = {db.get(card_id).full_name: db.get(card_id) for card_id in deck_card_ids}
    return {
        name: gaps
        for name, card in cards.items()
        if (gaps := card_effect_gaps(card))
    }