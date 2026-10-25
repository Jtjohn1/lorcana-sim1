"""Game engine: setup, turn structure, legal actions, and applying them.

Scope (see README for current gaps): this implements core turn structure,
major keywords (including all Shift variants, Adventurous, and Ward),
ink-drop counters, generic activated abilities, and a growing library of
named triggered/static effects (see `lorcana/card_effects.py`). It does NOT
implement every one of the 1,558 cards' unique text — see README "Known
gaps". The registries below
(`TRIGGERED_ABILITY_HANDLERS`, `ACTION_EFFECT_HANDLERS`,
`ACTIVATED_ABILITY_HANDLERS`, `STATIC_READY_BLOCKERS`,
`STATIC_LORE_MODIFIERS`, `STATIC_DAMAGE_SOURCE_OVERRIDE`,
`STATIC_TURN_START_DISCOUNT`, and `STATIC_GRANTED_ACTIVATED_ABILITIES`) are
where card-specific text plugs in
without touching the turn-structure code.
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import Any, Callable

from .cards import Card, CardDatabase
from .state import GameState, GameConfig, PlayerState, CardInstance

Action = dict[str, Any]  # {"kind": "...", ...fields}. See README for the action catalog.

# --- Extension points for card-specific text --------------------------------
#
# TRIGGERED_ABILITY_HANDLERS: keyed by (card_full_name, event). `event` is one
#   of "on_play", "on_quest", "on_challenge", "on_ready", "on_start_of_turn",
#   "on_banish", "on_banish_in_challenge", "on_ink_drop_removed", "on_move",
#   "on_character_moves_here", "on_ally_quest" (fires on each of the SAME
#   controller's OTHER characters whenever one quests — e.g. Mickey Mouse's
#   Secret Path), and "on_end_of_turn". Handler
#   signature: f(state, iid, **ctx) — `iid` is the listening card; ctx may
#   include `source_iid` (for ally-broadcast events) and other context
#   (e.g. `used_shift` for on_play).
# ACTION_EFFECT_HANDLERS: keyed by card full_name. f(state, iid).
# ACTIVATED_ABILITY_HANDLERS: keyed by the ability's bolded name (e.g.
#   "BREAKING RECORDS"). f(state, iid) — cost is already paid by the engine
#   by the time this runs (see `_parse_activated_costs`).
# STATIC_READY_BLOCKERS: keyed by full_name. f(state, iid) -> bool, True
#   means this instance does NOT ready in its controller's Ready step.
# STATIC_LORE_MODIFIERS: keyed by full_name. f(state, iid) -> int bonus
#   applied to this character's Lore value when it quests.
# STATIC_DAMAGE_SOURCE_OVERRIDE: keyed by full_name -> "willpower" (only
#   value needed so far). If the attacking/defending character's controller
#   has this card in play, that controller's characters deal challenge
#   damage using Willpower instead of Strength (see Dale — Ready for His
#   Shot). Simplification: Strength-based modifiers (Challenger, Support,
#   delayed mods) are ignored while this override is active — see README.
# STATIC_TURN_START_DISCOUNT: keyed by full_name -> int. Added to
#   `next_character_discount` automatically at the start of each of this
#   card's controller's turns (e.g. Grandmother Willow).
TRIGGERED_ABILITY_HANDLERS: dict[tuple[str, str], Callable] = {}
ACTION_EFFECT_HANDLERS: dict[str, Callable] = {}
ACTIVATED_ABILITY_HANDLERS: dict[str, Callable] = {}
ACTIVATED_ABILITY_LEGALITY: dict[str, Callable] = {}
STATIC_CARD_RESOLVERS: set[str] = set()
STATIC_READY_BLOCKERS: dict[str, Callable] = {}
STATIC_LORE_MODIFIERS: dict[str, Callable] = {}
STATIC_DAMAGE_SOURCE_OVERRIDE: dict[str, str] = {}
STATIC_TURN_START_DISCOUNT: dict[str, int] = {}
STATIC_GRANTED_ACTIVATED_ABILITIES: dict[str, Callable] = {}


def _queue_trigger(state: GameState, iid: int, handler: Callable, event: str, **ctx) -> None:
    state.triggered_bag.append({
        "owner": state.instances[iid].owner,
        "iid": iid,
        "handler": handler,
        "event": event,
        "ctx": dict(ctx),
    })


def _simple_generic_effect_body(
    text: str,
) -> tuple[str, int | tuple[int, int], bool] | None:
    """Parse a narrow set of complete, unconditional effect bodies."""
    body = " ".join(text.lower().split())
    body = re.sub(r"\s*\([^)]*\)", " ", body).strip()
    body = re.sub(r"\s+", " ", body).rstrip(" .")
    optional = body.startswith("you may ")
    if optional:
        body = body[len("you may "):].strip()

    draw_discard = re.fullmatch(
        r"draw (a|one|\d+) cards?, then choose and discard (a|one|\d+) cards?",
        body,
    )
    if draw_discard:
        draw_count = 1 if draw_discard.group(1) in ("a", "one") else int(draw_discard.group(1))
        discard_count = 1 if draw_discard.group(2) in ("a", "one") else int(draw_discard.group(2))
        return "draw_discard", (draw_count, discard_count), optional
    draw_match = re.fullmatch(r"draw (a|one|\d+) cards?", body)
    if draw_match:
        amount = 1 if draw_match.group(1) in ("a", "one") else int(draw_match.group(1))
        return "draw", amount, optional
    drop_match = re.fullmatch(r"get (\d+) ink drops?", body)
    if drop_match:
        return "ink_drops", int(drop_match.group(1)), optional
    opponent_discard = body == "each opponent chooses and discards a card"
    if opponent_discard:
        return "opponent_discard", 1, optional
    chosen_action_discard = (
        body == "chosen opponent reveals their hand and discards an action card of your choice"
    )
    if chosen_action_discard:
        return "opponent_discards_action_chosen", 1, optional
    player_draw = body == "each player draws a card"
    if player_draw:
        return "each_player_draw", 1, optional
    each_opponent_draw = body == "each opponent draws a card"
    if each_opponent_draw:
        return "each_opponent_draw", 1, optional

    damage_each = re.fullmatch(r"deal (\d+) damage to each opposing character", body)
    if damage_each:
        return "damage_each_opposing", int(damage_each.group(1)), optional
    damage_chosen = re.fullmatch(
        r"deal (\d+) damage to chosen (?:(opposing) )?character", body
    )
    if damage_chosen:
        return (
            "damage_opposing" if damage_chosen.group(2) else "damage_chosen",
            int(damage_chosen.group(1)), optional,
        )
    heal = re.fullmatch(r"remove up to (\d+) damage from chosen character", body)
    if heal:
        return "heal_chosen", int(heal.group(1)), optional
    exert = body == "exert chosen opposing character"
    if exert:
        return "exert_opposing", 1, optional
    banish = body == "banish chosen opposing character"
    if banish:
        return "banish_opposing", 1, optional
    banish_damaged = body == "banish all opposing damaged characters"
    if banish_damaged:
        return "banish_all_damaged_opposing", 1, optional
    if body == "ready this character":
        return "ready_self", 1, optional

    lore_match = re.fullmatch(r"gain (\d+) lore", body)
    if lore_match:
        return "lore", int(lore_match.group(1)), optional
    return None


def _simple_generic_trigger_effect(
    ability, event: str,
) -> tuple[str, int | tuple[int, int], bool] | None:
    """Recognize complete, unconditional trigger templates."""
    if ability.type != "triggered" or not ability.effect:
        return None
    prefixes = {
        "on_play": r"^when you play this character,\s*(.*)$",
        "on_play_item": r"^when you play this item,\s*(.*)$",
        "on_play_location": r"^when you play this location,\s*(.*)$",
        "on_play_action": r"^(?:once during your turn,\s*)?whenever you play an action,\s*(.*)$",
        "on_quest": r"^whenever this character quests,\s*(.*)$",
        "on_banish_in_challenge": r"^when this character is banished in a challenge,\s*(.*)$",
        "on_banish": r"^when this character is banished,\s*(.*)$",
        "on_being_challenged": r"^whenever this character is challenged,\s*(.*)$",
    }
    prefix = prefixes.get(event)
    if prefix is None:
        return None
    text = " ".join(ability.effect.lower().split())
    match = re.match(prefix, text)
    if match is None:
        return None
    return _simple_generic_effect_body(match.group(1))


def _simple_generic_static_strength_effect(ability) -> tuple[bool, str | None, int] | None:
    if ability.type != "static" or not ability.effect:
        return None
    text = " ".join(ability.effect.lower().split()).rstrip(" .")
    match = re.fullmatch(
        r"your (?:(other) )?(?:(.+?) )?characters get \+(\d+) (?:¤|strength)",
        text,
    )
    if match is None:
        return None
    other_only = bool(match.group(1))
    subtype = match.group(2)
    return other_only, subtype, int(match.group(3))


def _simple_generic_static_resist_effect(ability) -> tuple[str, bool, str | None, int] | None:
    if ability.type != "static" or not ability.effect:
        return None
    text = " ".join(ability.effect.lower().split())
    text = re.sub(r"\s*\([^)]*\)", " ", text).strip().rstrip(" .")
    personal = re.fullmatch(
        r"while you have no cards in your hand, this character gains resist \+(\d+)",
        text,
    )
    if personal:
        return "empty_hand", False, None, int(personal.group(1))
    global_effect = re.fullmatch(
        r"during opponents'? turns, your (?:(other) )?(?:(.+?) )?characters "
        r"gain resist \+(\d+)",
        text,
    )
    if global_effect:
        return (
            "opponents_turn", bool(global_effect.group(1)),
            global_effect.group(2), int(global_effect.group(3)),
        )
    return None


def _simple_generic_named_play_discount(ability) -> tuple[str, int] | None:
    if ability.type != "static" or not ability.effect:
        return None
    text = " ".join(ability.effect.lower().split()).rstrip(" .")
    match = re.fullmatch(
        r"if you have a character named (.+?) in play, you pay (\d+) ⬡ "
        r"less to play this character",
        text,
    )
    return (match.group(1), int(match.group(2))) if match else None


def _simple_generic_static_ready_threshold(ability) -> int | None:
    if ability.type != "static" or not ability.effect:
        return None
    text = " ".join(ability.effect.lower().split()).rstrip(" .")
    match = re.fullmatch(
        r"if you have (\d+) or more cards in your hand, this character can't ready",
        text,
    )
    return int(match.group(1)) if match else None


def _simple_generic_activated_effect(
    ability,
) -> tuple[str, int | tuple[int, int], bool] | None:
    if ability.type != "activated" or not ability.effect:
        return None
    return _simple_generic_effect_body(ability.effect)


def _resolve_simple_generic_effect(
    state: GameState, iid: int, *, generic_effect: str, generic_amount: int | tuple[int, int],
    generic_optional: bool = False, **ctx,
) -> None:
    owner = state.instances[iid].owner
    if generic_optional:
        if state.decision_maker is None:
            resolve = True
        else:
            resolve = state.choose(
                owner, f"generic_optional:{owner}:{generic_effect}", [True, False]
            )
        if not resolve:
            state.log_event(f"{state.card_of(iid).full_name} declines generic {generic_effect}")
            return

    if generic_effect == "draw":
        draw_cards(state, owner, generic_amount)
    elif generic_effect == "draw_discard":
        draw_count, discard_count = generic_amount
        draw_cards(state, owner, draw_count)
        _discard_cards_from_hand(state, owner, discard_count)
    elif generic_effect == "ink_drops":
        gain_ink_drops(state, owner, generic_amount)
    elif generic_effect == "each_player_draw":
        for pidx in range(len(state.players)):
            draw_cards(state, pidx, generic_amount)
    elif generic_effect == "each_opponent_draw":
        draw_cards(state, state.opponent_idx(owner), generic_amount)
    elif generic_effect == "opponent_discard":
        opponent = state.opponent_idx(owner)
        _discard_cards_from_hand(state, opponent, generic_amount)
    elif generic_effect == "opponent_discards_action_chosen":
        opponent = state.opponent_idx(owner)
        candidates = [
            iid for iid in state.players[opponent].hand
            if state.card_of(iid).type == "Action"
        ]
        choice = state.choose(owner, "discard_opponent_action", candidates)
        if choice in candidates:
            state.move_zone(choice, "discard")
    elif generic_effect == "damage_each_opposing":
        for target in state.characters_in_play(state.opponent_idx(owner)):
            deal_damage(state, target, generic_amount)
    elif generic_effect in ("damage_opposing", "damage_chosen"):
        targets = (
            state.characters_in_play(state.opponent_idx(owner))
            if generic_effect == "damage_opposing" else
            state.characters_in_play(0) + state.characters_in_play(1)
        )
        target = state.choose(owner, f"effect_damage_target:{owner}:{generic_amount}", targets)
        if target is not None:
            deal_damage(state, target, generic_amount)
    elif generic_effect == "heal_chosen":
        targets = state.characters_in_play(0) + state.characters_in_play(1)
        target = state.choose(owner, f"effect_heal_target:{owner}", targets)
        if target is not None:
            state.instances[target].damage = max(
                0, state.instances[target].damage - generic_amount
            )
    elif generic_effect == "exert_opposing":
        targets = state.characters_in_play(state.opponent_idx(owner))
        target = state.choose(owner, f"effect_exert_target:{owner}", targets)
        if target is not None:
            state.instances[target].exerted = True
    elif generic_effect == "banish_opposing":
        targets = state.characters_in_play(state.opponent_idx(owner))
        target = state.choose(owner, f"effect_banish_target:{owner}", targets)
        if target is not None:
            banish(state, target)
    elif generic_effect == "banish_all_damaged_opposing":
        targets = [
            target for target in state.characters_in_play(state.opponent_idx(owner))
            if state.instances[target].damage > 0
        ]
        for target in targets:
            banish(state, target)
    elif generic_effect == "ready_self":
        state.instances[iid].exerted = False
    elif generic_effect == "lore":
        state.players[owner].lore += generic_amount
    else:
        raise ValueError(f"Unknown generic triggered effect: {generic_effect}")
    state.log_event(
        f"{state.card_of(iid).full_name} resolves generic {generic_effect} ({generic_amount})"
    )


def _queue_registered_trigger(state: GameState, iid: int, event: str, **ctx) -> None:
    card = state.card_of(iid)
    handler = TRIGGERED_ABILITY_HANDLERS.get((card.full_name, event))
    if handler is None:
        handler = TRIGGERED_ABILITY_HANDLERS.get((card.name, event))
    if handler:
        _queue_trigger(state, iid, handler, event, **ctx)
        return

    # Handle only complete, unconditional templates. A card with an explicit
    # handler stays on that handler so its choice and timing semantics remain
    # authoritative.
    for ability in card.abilities:
        spec = _simple_generic_trigger_effect(ability, event)
        if spec is None:
            continue
        effect, amount, optional = spec
        once_per_turn = bool(
            ability.effect and "once during your turn" in ability.effect.lower()
        )
        if once_per_turn:
            owner = state.instances[iid].owner
            turn_started = state.players[owner].turns_started
            key = (iid, event)
            if state.active_idx != owner or state.once_per_turn_trigger_usage.get(key) == turn_started:
                continue
            state.once_per_turn_trigger_usage[key] = turn_started
        _queue_trigger(
            state, iid, _resolve_simple_generic_effect, event,
            generic_effect=effect, generic_amount=amount, generic_optional=optional, **ctx,
        )

def _permanents_in_play(state: GameState, player_idx: int) -> list[int]:
    """Active cards in play, excluding cards buried under a Shift stack."""
    return [
        iid for iid in state.players[player_idx].play
        if state.instances[iid].stacked_on is None
        and state.card_of(iid).type in ("Character", "Item", "Location")
    ]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def new_game(
    db: CardDatabase,
    deck_a_card_ids: list[int],
    deck_b_card_ids: list[int],
    config: GameConfig | None = None,
    seed: int | None = None,
    mulligan_decider: Callable[[GameState, int, list[int]], list[int]] | None = None,
) -> GameState:
    """Build a fresh game. Each deck list is a list of card ids (duplicates
    allowed, e.g. 4x the same id) — the caller is responsible for deck
    legality (60+ cards, <=2 ink types, <=4 copies, etc.)."""
    config = config or GameConfig()
    state = GameState(db, config, seed=seed)
    state.players = [PlayerState(0, "Player 0"), PlayerState(1, "Player 1")]

    for pidx, card_ids in enumerate((deck_a_card_ids, deck_b_card_ids)):
        for cid in card_ids:
            inst = state.new_instance(cid, pidx, "deck")
            state.players[pidx].deck.append(inst.instance_id)
        state.rng.shuffle(state.players[pidx].deck)

    state.active_idx = state.rng.randrange(2)
    state.first_player_idx = state.active_idx
    state.turn_number = 1

    for pidx in range(2):
        for _ in range(config.starting_hand_size):
            _draw_card(state, pidx)

    if config.allow_mulligan and mulligan_decider is not None:
        altered_players = []
        for pidx in (state.active_idx, state.opponent_idx(state.active_idx)):
            player = state.players[pidx]
            selected = list(mulligan_decider(state, pidx, list(player.hand)))
            if len(selected) != len(set(selected)) or any(iid not in player.hand for iid in selected):
                raise ValueError(f"Invalid mulligan selection for player {pidx}: {selected}")
            if not selected:
                continue
            for iid in selected:
                state.move_zone(iid, "deck")  # returned cards go to the bottom
            while len(player.hand) < config.starting_hand_size:
                if not player.deck:
                    break
                _draw_card(state, pidx)
            altered_players.append(pidx)
        for pidx in altered_players:
            state.rng.shuffle(state.players[pidx].deck)

    state.phase = "start_of_turn"
    _run_start_of_turn(state, skip_draw=True)  # first turn: ready/set only, no draw
    return state


def _draw_card(state: GameState, pidx: int) -> None:
    p = state.players[pidx]
    if not p.deck:
        return  # can't draw from an empty deck; loss is checked at end of turn
    iid = p.deck[0]
    state.move_zone(iid, "hand")


def draw_cards(state: GameState, pidx: int, n: int) -> None:
    """Public helper for card_effects.py: draw up to n cards (stops early,
    without crashing, if the deck runs out — see `_draw_card`)."""
    for _ in range(n):
        _draw_card(state, pidx)


def _discard_cards_from_hand(state: GameState, pidx: int, amount: int) -> None:
    for _ in range(amount):
        hand = list(state.players[pidx].hand)
        if not hand:
            return
        choice = state.choose(pidx, "discard_choice", hand)
        if choice not in hand:
            return
        state.move_zone(choice, "discard")


def gain_ink_drops(state: GameState, pidx: int, amount: int = 1) -> None:
    """Give a player single-use ink-drop counters (not card instances)."""
    if amount < 0:
        raise ValueError("Cannot gain a negative number of ink drops")
    state.players[pidx].ink_drops += amount
    if amount:
        state.log_event(f"Player {pidx} gets {amount} ink drop(s)")


def remove_ink_drops(state: GameState, pidx: int, amount: int = 1) -> None:
    """Remove ink-drop counters and queue abilities that watch their removal."""
    if amount < 0 or state.players[pidx].ink_drops < amount:
        raise ValueError("Not enough ink drops")
    state.players[pidx].ink_drops -= amount
    if amount:
        state.log_event(f"Player {pidx} removes {amount} ink drop(s)")
        for iid in _permanents_in_play(state, pidx):
            for _ in range(amount):
                _queue_registered_trigger(state, iid, "on_ink_drop_removed")


def banish(state: GameState, iid: int) -> None:
    """Public alias of `_banish` for card_effects.py."""
    _banish(state, iid)


def deal_damage(
    state: GameState, iid: int, amount: int, *, ignore_resist: bool = False
) -> int:
    """Deal damage and apply the target's Resist unless the effect says otherwise."""
    if amount <= 0:
        return 0
    dealt = amount if ignore_resist else _damage_after_resist(state, iid, amount)
    if dealt:
        state.instances[iid].damage += dealt
    return dealt

def move_damage(state: GameState, from_iid: int, to_iid: int, amount: int) -> int:
    """Moves up to `amount` damage counters from one character to another.
    Returns the amount actually moved."""
    src = state.instances[from_iid]
    moved = max(0, min(amount, src.damage))
    src.damage -= moved
    state.instances[to_iid].damage += moved
    return moved


def put_character_into_play_free(state: GameState, hand_iid: int) -> int:
    """Plays a character from hand paying no ink cost at all (e.g. Pocahontas
    - Guiding the Tribe's "play a character with cost 1 for free"). Still
    fires that character's own on_play trigger. Shift is not a legal alternative
    when a card is played from another zone; optional exerted entry is offered.
    """
    card = state.card_of(hand_iid)
    state.move_zone(hand_iid, "play")
    inst = state.instances[hand_iid]
    inst.entered_play_turn = state.players[inst.owner].turns_started
    inst.exerted = bool(
        _may_enter_exerted(card)
        and state.choose(inst.owner, "enter_exerted", [False, True])
    )
    state.log_event(f"Played {card.full_name} for free")
    _fire_self(state, "on_play", hand_iid, used_shift=False)
    return hand_iid


# ---------------------------------------------------------------------------
# Turn structure
# ---------------------------------------------------------------------------

def _run_start_of_turn(state: GameState, skip_draw: bool = False) -> None:
    p = state.active_player()
    pidx = state.active_idx
    p.turns_started += 1

    # Per-turn trackers reset now.
    state.cards_discarded_this_turn[pidx] = 0
    state.active_player_challenged_this_turn = False
    state.boost_used_this_turn = set()
    p.inked_this_turn = False
    if state.global_no_challenge_until_owner == pidx:
        state.global_no_challenge_until_owner = None
    state.delayed_strength_mods = [m for m in state.delayed_strength_mods if m["owner"] != pidx]
    state.delayed_challenger_mods = [m for m in state.delayed_challenger_mods if m["owner"] != pidx]
    state.delayed_resist_mods = [m for m in state.delayed_resist_mods if m["owner"] != pidx]

    # "At the start of your turn" triggers and triggers caused by Ready are
    # both placed into the bag now, then resolve after the Set step.
    for iid in _permanents_in_play(state, pidx):
        _queue_registered_trigger(state, iid, "on_start_of_turn")

    # Ready step (skip characters blocked by a registered STATIC_READY_BLOCKER,
    # e.g. Demona - Scourge of the Wyvern Clan's "Stone by Day").
    for iid in list(p.play) + list(p.inkwell):
        if iid in p.play and state.instances[iid].stacked_on is not None:
            continue  # cards under a Shift are not active cards in play
        card = state.card_of(iid)
        blocker = STATIC_READY_BLOCKERS.get(card.full_name)
        generic_threshold = next(
            (
                threshold for ability in card.abilities
                if (threshold := _simple_generic_static_ready_threshold(ability)) is not None
            ),
            None,
        )
        if (blocker and blocker(state, iid)) or (
            generic_threshold is not None
            and len(state.players[pidx].hand) >= generic_threshold
        ):
            state.log_event(f"{card.full_name} doesn't ready")
            continue
        was_exerted = state.instances[iid].exerted
        state.instances[iid].exerted = False
        if was_exerted and iid in p.play:
            _queue_registered_trigger(state, iid, "on_ready")

    # Set step: characters played before this controller turn are now dry
    # (handled by comparing entered_play_turn with turns_started); locations with a
    # lore value grant lore now; per-turn static discounts (e.g. Grandmother
    # Willow) are pre-loaded into the discount pool.
    for iid in state.characters_in_play(pidx):
        card = state.card_of(iid)
        disc = STATIC_TURN_START_DISCOUNT.get(card.full_name)
        if disc:
            state.next_character_discount += disc
            state.log_event(f"{card.full_name} grants a {disc}-ink discount on your next character this turn")
    for iid in state.locations_in_play(pidx):
        card = state.card_of(iid)
        if card.lore:
            p.lore += card.lore
            state.log_event(f"{card.full_name} grants {card.lore} lore (location)")
    _check_win(state)

    # Start-of-turn and Ready triggers resolve after the Set step.
    _resolve_trigger_bag(state)
    if state.winner is not None:
        return

    # Draw step
    if not skip_draw:
        if not p.deck:
            state.log_event(f"Player {pidx} has no cards to draw")
        else:
            _draw_card(state, pidx)
    state.phase = "main"


def _run_end_of_turn(state: GameState) -> None:
    state.phase = "end_of_turn"
    pidx = state.active_idx

    # End-of-turn abilities share the bag, so the active player can choose
    # their resolution order. Temporary Shift returns are delayed triggers too.
    for iid in _permanents_in_play(state, pidx):
        _queue_registered_trigger(state, iid, "on_end_of_turn")
    for iid in sorted(state.temporary_shift_iids):
        if state.instances[iid].owner == pidx and state.instances[iid].zone == "play":
            _queue_trigger(state, iid, _temporary_shift_end_trigger, "temporary_shift_end")
    _resolve_trigger_bag(state)
    _game_state_check(state)
    if state.winner is not None:
        return

    # "This turn" effects (Comprehensive Rules 6.1.13.4) expire now, at the
    # active player's End-of-Turn Phase.
    state.temp_strength_bonus.clear()
    state.temp_challenger_bonus.clear()
    state.next_character_discount = 0

    p = state.active_player()
    if not p.deck:
        state.winner = state.opponent_idx(pidx)
        state.phase = "game_over"
        state.log_event(f"Player {pidx} has 0 cards in deck at end of turn — loses")
        return
    state.active_idx = state.opponent_idx(pidx)
    state.turn_number += 1
    state.phase = "start_of_turn"
    _run_start_of_turn(state)


def _check_win(state: GameState) -> None:
    for i, p in enumerate(state.players):
        if p.lore >= 20 and state.winner is None:
            state.winner = i
            state.phase = "game_over"
            state.log_event(f"Player {i} reaches {p.lore} lore and wins")


def _check_banishments(state: GameState, *, in_challenge: bool = False) -> None:
    for iid, inst in list(state.instances.items()):
        if inst.zone != "play":
            continue
        card = state.db.get(inst.card_id)
        if card.type not in ("Character", "Location"):
            continue
        if inst.stacked_on is not None:
            continue  # buried under a Shift stack; not "in play"
        willpower = card.willpower or 0
        if inst.damage >= willpower:
            state.log_event(f"{card.full_name} banished ({inst.damage}/{willpower} damage)")
            _banish(state, iid, in_challenge=in_challenge)


def _banish(state: GameState, iid: int, *, in_challenge: bool = False) -> None:
    inst = state.instances[iid]
    if inst.stacked_on is None:
        _queue_registered_trigger(state, iid, "on_banish")
        if in_challenge:
            _queue_registered_trigger(state, iid, "on_banish_in_challenge")
    # Shift stacks: banishing the top card sends the whole stack to discard
    # (see Comprehensive Rules 8.10.7); we don't yet re-surface the
    # underneath card as "in play" since that's not how Shift works anyway.
    stack = [iid] + list(inst.stacked_under)
    state.temporary_shift_iids.difference_update(stack)
    # Cards tucked "under" this character (Boost) go to discard too.
    under_cards = [uid for uid, uinst in state.instances.items() if uinst.boosted_under == iid]
    for sid in stack + under_cards:
        state.move_zone(sid, "discard")


def _resolve_trigger_bag(state: GameState, *, in_challenge: bool = False) -> None:
    """Resolve queued triggers under the two-player active-player bag order."""
    player_order = (state.active_idx, state.opponent_idx(state.active_idx))
    while state.triggered_bag and state.winner is None:
        owner = next(
            (pidx for pidx in player_order if any(t["owner"] == pidx for t in state.triggered_bag)),
            None,
        )
        if owner is None:
            break
        options = [trigger for trigger in state.triggered_bag if trigger["owner"] == owner]
        chosen = state.choose(owner, "trigger_order", options)
        if chosen not in options:
            chosen = options[0]
        state.triggered_bag.remove(chosen)
        chosen["handler"](state, chosen["iid"], **chosen["ctx"])
        _game_state_check(state, in_challenge=in_challenge)


def _temporary_shift_end_trigger(state: GameState, iid: int) -> None:
    _return_temporary_shifts(state, state.instances[iid].owner, iid)


def _game_state_check(state: GameState, *, in_challenge: bool = False) -> None:
    _check_banishments(state, in_challenge=in_challenge)
    _check_win(state)


# ---------------------------------------------------------------------------
# Keyword helpers
# ---------------------------------------------------------------------------

def _has_kw(card: Card, kw: str) -> bool:
    return card.has_keyword(kw)


def _may_enter_exerted(card: Card) -> bool:
    """Bodyguard, and any other static ability whose reminder text says
    "may enter play exerted" (e.g. Mickey Mouse - Expedition Leader's Long
    Journey) — detected generically from the ability text rather than
    hardcoded per card."""
    if _has_kw(card, "Bodyguard"):
        return True
    return any(a.effect and "enter play exerted" in a.effect.lower() for a in card.abilities)


def _can_quest(state: GameState, iid: int) -> bool:
    inst = state.instances[iid]
    card = state.card_of(iid)
    if card.type != "Character" or inst.stacked_on is not None:
        return False
    if inst.exerted:
        return False
    if not state.is_dry(iid):
        return False
    if _has_kw(card, "Reckless"):
        return False
    return True


def _can_challenge_as_attacker(state: GameState, iid: int) -> bool:
    if state.global_no_challenge_until_owner is not None:
        return False
    inst = state.instances[iid]
    card = state.card_of(iid)
    if card.type != "Character" or inst.stacked_on is not None:
        return False
    if inst.exerted:
        return False
    if _has_kw(card, "Adventurous"):
        return False
    if state.is_dry(iid):
        return True
    return _has_kw(card, "Rush")  # Rush: can challenge the turn it's played


def _challengeable_characters(state: GameState, defender_idx: int, attacker_iid: int) -> list[int]:
    attacker_card = state.card_of(attacker_iid)
    attacker_evasive_ok = _has_kw(attacker_card, "Evasive") or _has_kw(attacker_card, "Alert")

    exerted_targets = []
    for iid in state.characters_in_play(defender_idx):
        inst = state.instances[iid]
        if not inst.exerted:
            continue
        card = state.card_of(iid)
        if _has_kw(card, "Evasive") and not attacker_evasive_ok:
            continue
        exerted_targets.append(iid)

    bodyguards = [iid for iid in exerted_targets if _has_kw(state.card_of(iid), "Bodyguard")]
    return bodyguards if bodyguards else exerted_targets


def _damage_source_override(state: GameState, controller_idx: int) -> str | None:
    for iid in state.characters_in_play(controller_idx):
        card = state.card_of(iid)
        override = STATIC_DAMAGE_SOURCE_OVERRIDE.get(card.full_name)
        if override:
            return override
    return None


def _effective_strength(state: GameState, iid: int, is_challenger: bool) -> int:
    card = state.card_of(iid)
    owner = state.instances[iid].owner

    override = _damage_source_override(state, owner)
    if override == "willpower":
        # SPIKE SUIT-style effect: damage dealt uses Willpower instead of
        # Strength. Simplification: Strength-specific modifiers (Challenger,
        # Support, delayed mods) don't apply since they buff a stat that's no
        # longer the damage source — see README.
        return max(card.willpower or 0, 0)

    strength = card.strength or 0
    strength += _generic_strength_bonus(state, iid)
    strength += state.temp_strength_bonus.get(iid, 0)
    strength += sum(m["amount"] for m in state.delayed_strength_mods if m["iid"] == iid)
    if is_challenger:
        strength += card.keyword_value("Challenger") or 0
        strength += state.temp_challenger_bonus.get(iid, 0)
        strength += sum(m["amount"] for m in state.delayed_challenger_mods if m["iid"] == iid)
    return max(strength, 0)


def _effective_lore(state: GameState, iid: int) -> int:
    card = state.card_of(iid)
    lore = max(card.lore or 0, 0)
    modifier = STATIC_LORE_MODIFIERS.get(card.full_name)
    if modifier:
        lore += modifier(state, iid)
    return max(lore, 0)


def _damage_after_resist(state: GameState, iid: int, incoming: int) -> int:
    card = state.card_of(iid)
    resist = (card.keyword_value("Resist") or 0) if _has_kw(card, "Resist") else 0
    resist += _generic_resist_bonus(state, iid)
    resist += sum(m["amount"] for m in state.delayed_resist_mods if m["iid"] == iid)
    return max(0, incoming - resist)


def _shift_abilities(card: Card):
    return [
        ability for ability in card.abilities
        if ability.type == "keyword" and ability.keyword and ability.keyword.endswith("Shift")
    ]


def _dual_name_parts(name: str) -> list[str]:
    return [part.strip() for part in name.split(" & ") if part.strip()]


def _shift_target_names(shift_card: Card, ability) -> list[str]:
    text = re.sub(r"\s+", " ", ability.full_text or "")
    names: list[str] = []
    pattern = re.compile(
        r"\bnamed\s+(.+?)(?=,\s*one named\b|,\s*or one of each\b|"
        r"\s+and one named\b|\s+or one named\b|\.\s+At the end\b|\)|$)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        fragment = match.group(1).strip(" .,;")
        names.extend(part.strip(" .,;") for part in re.split(r"\s+or\s+", fragment, flags=re.IGNORECASE))
    if not names:
        names = _dual_name_parts(shift_card.name)
    return list(dict.fromkeys(name for name in names if name))


def _card_has_name(card: Card, name: str) -> bool:
    return name == card.name or name in _dual_name_parts(card.name)


def _is_morph_target(card: Card) -> bool:
    return any(
        ability.type == "static" and ability.effect
        and "shift any character on top of this character" in ability.effect.lower()
        for ability in card.abilities
    )


def _shift_target_sets(state: GameState, player_idx: int, shift_card: Card, ability) -> list[list[int]]:
    """Legal one- or two-card targets for a specific Shift ability."""
    kw = ability.keyword
    candidates = [
        iid for iid in state.players[player_idx].play
        if state.instances[iid].stacked_on is None
    ]
    names = _shift_target_names(shift_card, ability)
    is_potato = kw == "Potato Shift"
    allowed = []
    for iid in candidates:
        target = state.card_of(iid)
        morph = target.type == "Character" and _is_morph_target(target)
        if is_potato:
            matches = (target.type == "Item" and target.name == "Potato") or morph
        elif kw == "Universal Shift":
            matches = target.type == "Character"
        elif kw in ("Duo Shift", "Combo Shift", "Shift", "Temporary Shift"):
            matches = target.type == "Character" and (morph or any(_card_has_name(target, name) for name in names))
        elif kw == "Temporary Red Panda Shift":
            matches = target.type == "Character" and (morph or "Red Panda" in target.subtypes)
        elif kw.endswith(" Shift"):
            classification = kw[:-len(" Shift")]
            matches = target.type == "Character" and (morph or classification in target.subtypes)
        else:
            matches = False
        if matches:
            allowed.append(iid)

    if kw == "Duo Shift":
        if len(names) < 2:
            return []
        return [
            [first, second]
            for index, first in enumerate(allowed)
            for second in allowed[index + 1:]
            if (
                (_is_morph_target(state.card_of(first)) or _card_has_name(state.card_of(first), names[0]))
                and (_is_morph_target(state.card_of(second)) or _card_has_name(state.card_of(second), names[1]))
            ) or (
                (_is_morph_target(state.card_of(first)) or _card_has_name(state.card_of(first), names[1]))
                and (_is_morph_target(state.card_of(second)) or _card_has_name(state.card_of(second), names[0]))
            )
        ]
    if kw == "Combo Shift":
        singles = [[iid] for iid in allowed if (
            _is_morph_target(state.card_of(iid))
            or any(_card_has_name(state.card_of(iid), name) for name in names)
        )]
        if len(names) < 2:
            return singles
        pairs = [
            [first, second]
            for index, first in enumerate(allowed)
            for second in allowed[index + 1:]
            if (
                (_is_morph_target(state.card_of(first)) or _card_has_name(state.card_of(first), names[0]))
                and (_is_morph_target(state.card_of(second)) or _card_has_name(state.card_of(second), names[1]))
            ) or (
                (_is_morph_target(state.card_of(first)) or _card_has_name(state.card_of(first), names[1]))
                and (_is_morph_target(state.card_of(second)) or _card_has_name(state.card_of(second), names[0]))
            )
        ]
        return singles + pairs
    return [[iid] for iid in allowed]


def _shift_payments(ability, ready_ink_count: int, ink_drops: int) -> list[dict]:
    alt_text = f"{ability.keyword_value or ''} {ability.full_text or ''}"
    remove_match = re.search(r"remove\s+(\d+)\s+ink drops", alt_text, re.IGNORECASE)
    if remove_match:
        amount = int(remove_match.group(1))
        return [{"shift_cost": 0, "shift_remove_drops": amount}] if ink_drops >= amount else []

    cost = ability.keyword_value_number
    if cost is None:
        value = ability.keyword_value or ""
        match = re.search(r"\d+", value)
        if match:
            cost = int(match.group(0))
    if cost is None:
        match = re.search(r"Shift\s+(\d+)\s*⬡", ability.full_text or "", re.IGNORECASE)
        if match:
            cost = int(match.group(1))
    if cost is None:
        return []
    return [
        {"shift_cost": cost, **({"ink_drops": drops} if drops else {})}
        for drops in _ink_drop_payment_options(ready_ink_count, ink_drops, cost)
    ]


def _return_temporary_shifts(state: GameState, player_idx: int, only_iid: int | None = None) -> None:
    candidates = [only_iid] if only_iid is not None else sorted(list(state.temporary_shift_iids))
    for iid in candidates:
        if iid not in state.temporary_shift_iids:
            continue
        inst = state.instances[iid]
        if inst.owner != player_idx or inst.zone != "play":
            state.temporary_shift_iids.discard(iid)
            continue

        upper_iid = inst.stacked_on
        lower = [
            candidate for candidate in inst.stacked_under
            if state.instances[candidate].zone == "play" and state.instances[candidate].stacked_on == iid
        ]
        top_iid = iid
        while state.instances[top_iid].stacked_on is not None:
            top_iid = state.instances[top_iid].stacked_on
        top = state.instances[top_iid]
        stack_ids = [top_iid] + list(top.stacked_under)
        for stack_iid in stack_ids:
            state.instances[stack_iid].damage = 0

        if upper_iid is not None:
            parent_iid = upper_iid
            for lower_iid in lower:
                state.instances[lower_iid].stacked_on = parent_iid
            for other in state.instances.values():
                if iid in other.stacked_under:
                    other.stacked_under.remove(iid)
        else:
            for lower_iid in lower:
                state.instances[lower_iid].stacked_on = None
                state.instances[lower_iid].exerted = inst.exerted
                state.instances[lower_iid].entered_play_turn = inst.entered_play_turn

        state.move_zone(iid, "hand")
        state.temporary_shift_iids.discard(iid)
        state.log_event(f"Temporary Shift returns {state.card_of(iid).full_name} to hand")


# --- Activated abilities -----------------------------------------------------

_INK_COST_RE = re.compile(r"^(\d+)\s*⬡$")


def _parse_activated_costs(costs: tuple[str, ...]) -> dict | None:
    """Turns an ability's raw `costs` tuple (e.g. ('⟳', '1 ⬡')) into a
    structured cost the engine can check/pay generically. Returns None if a
    cost fragment isn't recognized (the ability is then simply not offered
    as a legal action — see README "Known gaps", activated abilities)."""
    parsed = {"exert_self": False, "ink": 0, "banish_self": False}
    for c in costs:
        c = c.strip()
        if c == "⟳":
            parsed["exert_self"] = True
        elif _INK_COST_RE.match(c):
            parsed["ink"] = int(_INK_COST_RE.match(c).group(1))
        elif c.lower().startswith("banish this"):
            parsed["banish_self"] = True
        else:
            return None  # unrecognized cost fragment; don't offer this ability
    return parsed


def _activated_ability_actions(state: GameState, iid: int, ready_ink_count: int, ink_drops: int) -> list[Action]:
    card = state.card_of(iid)
    inst = state.instances[iid]
    out = []

    def offer(ability_name: str, costs: tuple[str, ...], *, granted_by: int | None = None) -> None:
        parsed = _parse_activated_costs(costs)
        if parsed is None:
            return
        legality = ACTIVATED_ABILITY_LEGALITY.get(ability_name)
        if legality is not None and not legality(state, iid):
            return
        usage_key = (iid, ability_name)
        ability_obj = next((a for a in card.abilities if a.type == "activated" and a.name == ability_name), None)
        once_per_turn = bool(ability_obj and ability_obj.effect and "once during your turn" in ability_obj.effect.lower())
        if once_per_turn and state.once_per_turn_activated_usage.get(usage_key) == state.players[inst.owner].turns_started:
            return
        if parsed["exert_self"]:
            if inst.exerted:
                return
            if card.type == "Character" and not state.is_dry(iid):
                return  # 4.4.2: exert-cost activated abilities need a dry character
        for drops in _ink_drop_payment_options(ready_ink_count, ink_drops, parsed["ink"]):
            action = {
                "kind": "activate_ability", "iid": iid, "ability_name": ability_name,
                "exert_self": parsed["exert_self"], "ink": parsed["ink"],
                "banish_self": parsed["banish_self"],
            }
            if drops:
                action["ink_drops"] = drops
            if granted_by is not None:
                action["granted_by"] = granted_by
            out.append(action)

    for ability in card.abilities:
        if ability.type != "activated" or not ability.name:
            continue
        if (
            ability.name not in ACTIVATED_ABILITY_HANDLERS
            and _simple_generic_activated_effect(ability) is None
        ):
            continue
        offer(ability.name, ability.costs)

    owner = inst.owner
    for source_iid in _permanents_in_play(state, owner):
        source_card = state.card_of(source_iid)
        grant = (
            STATIC_GRANTED_ACTIVATED_ABILITIES.get(source_card.full_name)
            or STATIC_GRANTED_ACTIVATED_ABILITIES.get(source_card.name)
        )
        if grant is None:
            continue
        for ability in grant(state, source_iid, iid):
            if ability["name"] not in ACTIVATED_ABILITY_HANDLERS:
                continue
            offer(ability["name"], tuple(ability.get("costs", ())), granted_by=source_iid)
    return out

def _do_activate_ability(state: GameState, action: Action) -> None:
    iid = action["iid"]
    card = state.card_of(iid)
    p = state.player_of(iid)
    ability_obj = next((a for a in card.abilities if a.type == "activated" and a.name == action["ability_name"]), None)
    if ability_obj and ability_obj.effect and "once during your turn" in ability_obj.effect.lower():
        state.once_per_turn_activated_usage[(iid, action["ability_name"])] = p.turns_started
    if action["exert_self"]:
        state.instances[iid].exerted = True
    if action["ink"]:
        _exert_ink(state, p, action["ink"], action.get("ink_drops", 0))
    state.log_event(f"{card.full_name} activates {action['ability_name']}")
    if action["banish_self"]:
        _banish(state, iid)
    handler = ACTIVATED_ABILITY_HANDLERS.get(action["ability_name"])
    if handler:
        handler(state, iid)
    else:
        ability = next(
            ability for ability in card.abilities
            if ability.type == "activated" and ability.name == action["ability_name"]
        )
        effect = _simple_generic_activated_effect(ability)
        if effect is None:
            raise ValueError(f"No resolver for activated ability {ability.name}")
        effect_kind, amount, optional = effect
        _resolve_simple_generic_effect(state, iid, generic_effect=effect_kind, generic_amount=amount, generic_optional=optional)


# --- Boost -------------------------------------------------------------------

def _boost_actions(state: GameState, iid: int, ready_ink_count: int, ink_drops: int) -> list[Action]:
    card = state.card_of(iid)
    if not _has_kw(card, "Boost"):
        return []
    if iid in state.boost_used_this_turn:
        return []
    cost = card.keyword_value("Boost") or 0
    return [
        {"kind": "boost", "iid": iid, "cost": cost, **({"ink_drops": drops} if drops else {})}
        for drops in _ink_drop_payment_options(ready_ink_count, ink_drops, cost)
    ]


def _do_boost(state: GameState, action: Action) -> None:
    iid = action["iid"]
    card = state.card_of(iid)
    p = state.player_of(iid)
    _exert_ink(state, p, action["cost"], action.get("ink_drops", 0))
    state.boost_used_this_turn.add(iid)
    if not p.deck:
        state.log_event(f"{card.full_name} boosts but the deck is empty")
        return
    top_iid = p.deck[0]
    state.move_zone(top_iid, "under")
    state.instances[top_iid].boosted_under = iid
    state.log_event(f"{card.full_name} boosts (puts a card under itself)")
    _queue_registered_trigger(state, iid, "on_boost")


# ---------------------------------------------------------------------------
# Legal actions
# ---------------------------------------------------------------------------

def _ink_drop_payment_options(ready_ink_count: int, ink_drops: int, cost: int) -> list[int]:
    """Return every legal number of ink drops to spend for a cost."""
    if cost < 0:
        return []
    return [
        drops for drops in range(min(ink_drops, cost) + 1)
        if cost - drops <= ready_ink_count
    ]


def _first_turn_card_discount(state: GameState, pidx: int, card: Card) -> int:
    """Resolve the Underdog discount for the non-starting player's first turn."""
    if pidx == state.first_player_idx or state.players[pidx].turns_started != 1:
        return 0
    for ability in card.abilities:
        if ability.type != "static" or ability.name != "UNDERDOG" or not ability.effect:
            continue
        text = " ".join(ability.effect.lower().split()).rstrip(" .")
        match = re.fullmatch(
            r"if this is your first turn and you're not the first player, "
            r"you pay (\d+) ⬡ less to play this character",
            text,
        )
        if match:
            return int(match.group(1))
    return 0


def _named_character_play_discount(state: GameState, pidx: int, card: Card) -> int:
    discount = 0
    names_in_play = {state.card_of(iid).name.lower() for iid in state.characters_in_play(pidx)}
    for ability in card.abilities:
        effect = _simple_generic_named_play_discount(ability)
        if effect and effect[0] in names_in_play:
            discount += effect[1]
    return discount


def _generic_strength_bonus(state: GameState, iid: int) -> int:
    target = state.card_of(iid)
    if target.type != "Character":
        return 0
    bonus = 0
    for owner in (0, 1):
        for source_iid in _permanents_in_play(state, owner):
            if state.instances[iid].owner != owner:
                continue
            source = state.card_of(source_iid)
            for ability in source.abilities:
                effect = _simple_generic_static_strength_effect(ability)
                if effect is None:
                    continue
                other_only, subtype, amount = effect
                if other_only and iid == source_iid:
                    continue
                if subtype and subtype not in {item.lower() for item in target.subtypes}:
                    continue
                bonus += amount
    return bonus


def _generic_resist_bonus(state: GameState, iid: int) -> int:
    target = state.card_of(iid)
    if target.type != "Character":
        return 0
    owner = state.instances[iid].owner
    bonus = 0
    for ability in target.abilities:
        effect = _simple_generic_static_resist_effect(ability)
        if effect and effect[0] == "empty_hand" and not state.players[owner].hand:
            bonus += effect[3]
    if state.active_idx != owner:
        for source_iid in _permanents_in_play(state, owner):
            source = state.card_of(source_iid)
            for ability in source.abilities:
                effect = _simple_generic_static_resist_effect(ability)
                if effect is None or effect[0] != "opponents_turn":
                    continue
                _, other_only, subtype, amount = effect
                if other_only and source_iid == iid:
                    continue
                if subtype and subtype not in {item.lower() for item in target.subtypes}:
                    continue
                bonus += amount
    return bonus


def legal_actions(state: GameState) -> list[Action]:
    if state.phase != "main" or state.winner is not None:
        return []
    p = state.active_player()
    actions: list[Action] = []

    # Ink a card (once per turn)
    if not p.inked_this_turn:
        for iid in p.hand:
            if state.card_of(iid).inkwell:
                actions.append({"kind": "ink", "hand_iid": iid})

    ready_ink_count = sum(1 for iid in p.inkwell if not state.instances[iid].exerted)
    ink_drops = p.ink_drops
    discount = state.next_character_discount

    def payment_fields(drops: int) -> dict:
        return {"ink_drops": drops} if drops else {}

    # Play cards from hand
    for iid in p.hand:
        card = state.card_of(iid)
        if card.type == "Character":
            card_discount = (
                discount + _first_turn_card_discount(state, p.player_id, card)
                + _named_character_play_discount(state, p.player_id, card)
            )
            effective_cost = max(0, card.cost - card_discount)
            for drops in _ink_drop_payment_options(ready_ink_count, ink_drops, effective_cost):
                action = {"kind": "play_character", "hand_iid": iid, "pay": "ink", **payment_fields(drops)}
                actions.append(action)
                if _may_enter_exerted(card):
                    actions.append({**action, "enter_exerted": True})
            for shift_ability in _shift_abilities(card):
                for target_ids in _shift_target_sets(state, state.active_idx, card, shift_ability):
                    for payment in _shift_payments(shift_ability, ready_ink_count, ink_drops):
                        action = {
                            "kind": "play_character", "hand_iid": iid, "pay": "shift",
                            "shift_keyword": shift_ability.keyword, **payment,
                        }
                        if len(target_ids) == 1:
                            action["target_iid"] = target_ids[0]
                        else:
                            action["target_iids"] = target_ids
                        actions.append(action)
        elif card.type in ("Item", "Location"):
            kind = "play_item" if card.type == "Item" else "play_location"
            for drops in _ink_drop_payment_options(ready_ink_count, ink_drops, card.cost):
                actions.append({"kind": kind, "hand_iid": iid, **payment_fields(drops)})
        elif card.type == "Action":
            is_song = "Song" in card.subtypes
            for drops in _ink_drop_payment_options(ready_ink_count, ink_drops, card.cost):
                actions.append({
                    "kind": "play_action", "hand_iid": iid, "pay": "ink", **payment_fields(drops),
                })
            if is_song:
                actions += _song_alt_cost_actions(state, iid, card)

    # Quest
    for iid in state.characters_in_play(state.active_idx):
        if _can_quest(state, iid):
            actions.append({"kind": "quest", "iid": iid})

    # Challenge (character or location target)
    opp_idx = state.opponent_idx(state.active_idx)
    for iid in state.characters_in_play(state.active_idx):
        if not _can_challenge_as_attacker(state, iid):
            continue
        for target in _challengeable_characters(state, opp_idx, iid):
            actions.append({"kind": "challenge", "attacker_iid": iid, "defender_iid": target, "target_type": "character"})
        for loc in state.locations_in_play(opp_idx):
            actions.append({"kind": "challenge", "attacker_iid": iid, "defender_iid": loc, "target_type": "location"})

    # Move a character to one of your locations
    for loc_iid in state.locations_in_play(state.active_idx):
        move_cost = state.card_of(loc_iid).move_cost or 0
        for drops in _ink_drop_payment_options(ready_ink_count, ink_drops, move_cost):
            for char_iid in state.characters_in_play(state.active_idx):
                if state.instances[char_iid].at_location != loc_iid:
                    actions.append({
                        "kind": "move", "char_iid": char_iid, "location_iid": loc_iid,
                        "cost": move_cost, **payment_fields(drops),
                    })

    # Activated abilities and Boost, for every card the active player controls in play
    for iid in p.play:
        if state.instances[iid].stacked_on is not None:
            continue
        actions += _activated_ability_actions(state, iid, ready_ink_count, ink_drops)
        actions += _boost_actions(state, iid, ready_ink_count, ink_drops)

    # End turn — mandatory Reckless challenges and Adventurous quests must be completed first.
    if not _must_challenge_with_reckless(state) and not _must_quest_with_adventurous(state):
        actions.append({"kind": "end_turn"})

    return actions


def _singing_cost(card: Card) -> int:
    singer_cost = card.keyword_value("Singer") if _has_kw(card, "Singer") else None
    return singer_cost if singer_cost is not None else card.cost


def _song_alt_cost_actions(state: GameState, song_iid: int, song_card: Card) -> list[Action]:
    """Offer legal single-singer and Sing Together payment groups."""
    out = []
    my_chars = [
        iid for iid in state.characters_in_play(state.active_idx)
        if state.is_dry(iid) and not state.instances[iid].exerted
    ]

    for iid in my_chars:
        if _singing_cost(state.card_of(iid)) >= song_card.cost:
            out.append({"kind": "play_action", "hand_iid": song_iid, "pay": "sing", "singers": [iid]})

    sing_together_n = next(
        (ability.keyword_value_number for ability in song_card.abilities if ability.keyword == "Sing Together"),
        None,
    )
    if sing_together_n is not None:
        for size in range(1, len(my_chars) + 1):
            for singers in combinations(my_chars, size):
                if sum(_singing_cost(state.card_of(iid)) for iid in singers) >= sing_together_n:
                    out.append({
                        "kind": "play_action", "hand_iid": song_iid,
                        "pay": "sing_together", "singers": list(singers),
                    })
    return out


def _must_quest_with_adventurous(state: GameState) -> bool:
    for iid in state.characters_in_play(state.active_idx):
        if _has_kw(state.card_of(iid), "Adventurous") and _can_quest(state, iid):
            return True
    return False


def _must_challenge_with_reckless(state: GameState) -> bool:
    for iid in state.characters_in_play(state.active_idx):
        card = state.card_of(iid)
        if _has_kw(card, "Reckless") and _can_challenge_as_attacker(state, iid):
            opp_idx = state.opponent_idx(state.active_idx)
            if _challengeable_characters(state, opp_idx, iid) or state.locations_in_play(opp_idx):
                return True
    return False


# ---------------------------------------------------------------------------
# Applying actions
# ---------------------------------------------------------------------------

def apply_action(state: GameState, action: Action, *, validate: bool = True) -> None:
    if state.winner is not None:
        return
    kind = action["kind"]
    if validate and action not in legal_actions(state):
        raise ValueError(f"Illegal action in {state.phase}: {action}")
    handler = {
        "ink": _do_ink,
        "play_character": _do_play_character,
        "play_item": _do_play_item,
        "play_location": _do_play_location,
        "play_action": _do_play_action,
        "quest": _do_quest,
        "challenge": _do_challenge,
        "move": _do_move,
        "activate_ability": _do_activate_ability,
        "boost": _do_boost,
        "end_turn": _do_end_turn,
    }.get(kind)
    if handler is None:
        raise ValueError(f"Unknown action kind: {kind}")
    handler(state, action)
    if kind != "end_turn":
        _game_state_check(state)
        _resolve_trigger_bag(state)


def _exert_ink(state: GameState, p: PlayerState, amount: int, ink_drops: int = 0) -> None:
    if amount < 0 or ink_drops < 0 or ink_drops > amount:
        raise ValueError("Invalid ink payment")
    if p.ink_drops < ink_drops:
        raise ValueError("Not enough ink drops")
    ink_to_exert = amount - ink_drops
    ready = [iid for iid in p.inkwell if not state.instances[iid].exerted]
    if len(ready) < ink_to_exert:
        raise ValueError("Not enough ready ink")
    if ink_drops:
        remove_ink_drops(state, p.player_id, ink_drops)
    for iid in ready[:ink_to_exert]:
        state.instances[iid].exerted = True


def _do_ink(state: GameState, action: Action) -> None:
    p = state.active_player()
    iid = action["hand_iid"]
    state.move_zone(iid, "inkwell")
    state.instances[iid].exerted = False
    p.inked_this_turn = True
    state.log_event(f"Inked {state.card_of(iid).full_name}")


def _do_play_character(state: GameState, action: Action) -> None:
    p = state.active_player()
    iid = action["hand_iid"]
    card = state.card_of(iid)
    used_shift = action["pay"] == "shift"

    if not used_shift:
        discount = (
            state.next_character_discount
            + _first_turn_card_discount(state, p.player_id, card)
            + _named_character_play_discount(state, p.player_id, card)
        )
        cost = max(0, card.cost - discount)
        _exert_ink(state, p, cost, action.get("ink_drops", 0))
        state.next_character_discount = 0  # consumed, regardless of amount used
    else:
        if action.get("shift_remove_drops"):
            remove_ink_drops(state, p.player_id, action["shift_remove_drops"])
        else:
            _exert_ink(state, p, action["shift_cost"], action.get("ink_drops", 0))

    state.move_zone(iid, "play")
    inst = state.instances[iid]
    inst.entered_play_turn = p.turns_started
    inst.exerted = False

    if used_shift:
        target_iids = action["target_iids"] if "target_iids" in action else [action["target_iid"]]
        target_instances = [state.instances[target_iid] for target_iid in target_iids]
        inst.stacked_under = []
        for target_iid, target in zip(target_iids, target_instances):
            target.stacked_on = iid
            inst.stacked_under.append(target_iid)
            inst.stacked_under.extend(target.stacked_under)
        # The new top card inherits the shared character state. A Potato item
        # has no exerted state, but its play turn determines whether the new
        # character is dry (Comprehensive Rules 8.10.4.1).
        inst.exerted = any(target.exerted for target in target_instances)
        entered_turns = [target.entered_play_turn for target in target_instances if target.entered_play_turn is not None]
        inst.entered_play_turn = max(entered_turns) if entered_turns else p.turns_started
        inst.damage = sum(target.damage for target in target_instances)
        locations = {target.at_location for target in target_instances if target.at_location is not None}
        inst.at_location = next(iter(locations)) if len(locations) == 1 else None
        # Effects that already applied to a character persist through Shift.
        # Keep the underlying entries too: they can become active again when a
        # Temporary Shift card returns to hand.
        inst_strength_bonus = sum(state.temp_strength_bonus.get(target_iid, 0) for target_iid in target_iids)
        if inst_strength_bonus:
            state.temp_strength_bonus[iid] = inst_strength_bonus
        inherited_delayed = [
            {**mod, "iid": iid}
            for mod in state.delayed_strength_mods
            if mod["iid"] in target_iids
        ]
        state.delayed_strength_mods.extend(inherited_delayed)
        for mods in (state.delayed_challenger_mods, state.delayed_resist_mods):
            inherited = [{**mod, "iid": iid} for mod in mods if mod["iid"] in target_iids]
            mods.extend(inherited)
        inherited_temp_challenger = sum(state.temp_challenger_bonus.get(target_iid, 0) for target_iid in target_iids)
        if inherited_temp_challenger:
            state.temp_challenger_bonus[iid] = inherited_temp_challenger
        joined = " and ".join(state.card_of(target_iid).full_name for target_iid in target_iids)
        state.log_event(f"Shifted {card.full_name} onto {joined}")
        if (action.get("shift_keyword") or "").startswith("Temporary"):
            state.temporary_shift_iids.add(iid)
    else:
        if _may_enter_exerted(card) and action.get("enter_exerted"):
            inst.exerted = True
        state.log_event(f"Played {card.full_name}")

    _fire_self(
        state, "on_play", iid,
        used_shift=used_shift,
        used_ink_drop=bool(action.get("ink_drops", 0)),
    )


def _do_play_item(state: GameState, action: Action) -> None:
    p = state.active_player()
    iid = action["hand_iid"]
    card = state.card_of(iid)
    _exert_ink(state, p, card.cost, action.get("ink_drops", 0))
    state.move_zone(iid, "play")
    state.instances[iid].entered_play_turn = p.turns_started
    state.log_event(f"Played item {card.full_name}")
    _queue_registered_trigger(state, iid, "on_play_item")


def _do_play_location(state: GameState, action: Action) -> None:
    p = state.active_player()
    iid = action["hand_iid"]
    card = state.card_of(iid)
    _exert_ink(state, p, card.cost, action.get("ink_drops", 0))
    state.move_zone(iid, "play")
    state.instances[iid].entered_play_turn = p.turns_started
    state.log_event(f"Played location {card.full_name}")
    _queue_registered_trigger(state, iid, "on_play_location")


def _generic_action_plan(card: Card) -> list[tuple[str, Any]] | None:
    """Parse a small, exact subset of common action-card effects.

    This is intentionally all-or-nothing: an unknown clause prevents the
    generic resolver from applying any part of the action.
    """
    text = " ".join(card.full_text.lower().split())
    text = re.sub(r"\s*\([^)]*\)", " ", text).strip()
    if card.has_keyword("Sing Together"):
        text = re.sub(r"^sing together \d+\s*", "", text)
    clauses = [part.strip().rstrip(" .") for part in re.split(r"\.\s*", text) if part.strip()]
    if not clauses:
        return None
    if re.fullmatch(
        r"deal 2 damage to chosen opposing character\. if you have a character named darkwing duck in play, deal 3 damage instead",
        text.rstrip(" ."),
    ):
        return [("damage_named_substitution", (2, 3, "Darkwing Duck"))]

    plan: list[tuple[str, Any]] = []
    for clause in clauses:
        draw = re.fullmatch(r"draw (a|one|\d+) cards?", clause)
        draw_discard = re.fullmatch(
            r"draw (a|one|\d+) cards?, then choose and discard (a|one|\d+) cards?",
            clause,
        )
        lore = re.fullmatch(r"gain (\d+) lore", clause)
        drops = re.fullmatch(r"get (\d+) ink drops?", clause)
        discount = re.fullmatch(
            r"you pay (\d+) ⬡ less for the next character you play this turn",
            clause,
        )
        bottom_cheap = re.fullmatch(
            r"put all opposing characters with (\d+) ¤ or less on the bottom of "
            r"their players' decks in any order",
            clause,
        )
        damage = re.fullmatch(
            r"deal (\d+) damage to chosen (?:(opposing) )?(?:(damaged) )?character",
            clause,
        )
        damage_equal_chars = clause == "deal damage to chosen character equal to the number of characters you have in play"
        challenger_gain = re.fullmatch(r"your (.+?) characters gain challenger \+(\d+) this turn", clause)
        exert = re.fullmatch(r"exert chosen (?:(opposing) )?character", clause)
        put_ink = clause == "put the top card of your deck into your inkwell facedown and exerted"
        put_damage = re.fullmatch(
            r"put (\d+) damage counters? on each opposing character", clause
        )
        play_character_from_discard = clause == "play a character from your discard for free"

        if damage_equal_chars:
            plan.append(("damage_equal_own_characters", None))
        elif challenger_gain:
            plan.append(("temp_challenger_subtype", (challenger_gain.group(1), int(challenger_gain.group(2)))))
        elif draw_discard:
            draw_count = 1 if draw_discard.group(1) in ("a", "one") else int(draw_discard.group(1))
            discard_count = 1 if draw_discard.group(2) in ("a", "one") else int(draw_discard.group(2))
            plan.append(("draw_discard", (draw_count, discard_count)))
        elif draw:
            amount = 1 if draw.group(1) in ("a", "one") else int(draw.group(1))
            plan.append(("draw", amount))
        elif lore:
            plan.append(("lore", int(lore.group(1))))
        elif drops:
            plan.append(("ink_drops", int(drops.group(1))))
        elif discount:
            plan.append(("next_character_discount", int(discount.group(1))))
        elif bottom_cheap:
            plan.append(("bottom_cheap_opposing", int(bottom_cheap.group(1))))
        elif damage:
            plan.append((
                "damage",
                (int(damage.group(1)), bool(damage.group(2)), bool(damage.group(3))),
            ))
        elif exert:
            plan.append(("exert", bool(exert.group(1))))
        elif put_ink:
            plan.append(("put_top_in_inkwell", None))
        elif put_damage:
            plan.append(("put_damage_each_opposing", int(put_damage.group(1))))
        elif play_character_from_discard:
            plan.append(("play_character_from_discard", None))
        else:
            return None
    return plan


def _resolve_generic_action_effect(state: GameState, iid: int, plan: list[tuple[str, Any]]) -> None:
    owner = state.instances[iid].owner
    opponent = state.opponent_idx(owner)
    for effect, value in plan:
        if effect == "draw":
            draw_cards(state, owner, value)
        elif effect == "draw_discard":
            draw_count, discard_count = value
            draw_cards(state, owner, draw_count)
            _discard_cards_from_hand(state, owner, discard_count)
        elif effect == "lore":
            state.players[owner].lore += value
        elif effect == "ink_drops":
            gain_ink_drops(state, owner, value)
        elif effect == "put_top_in_inkwell":
            deck = state.players[owner].deck
            if deck:
                top_iid = deck[0]
                state.move_zone(top_iid, "inkwell")
                state.instances[top_iid].exerted = True
        elif effect == "put_damage_each_opposing":
            for target in state.characters_in_play(opponent):
                state.instances[target].damage += value
        elif effect == "play_character_from_discard":
            options = [
                target for target in state.players[owner].discard
                if state.card_of(target).type == "Character"
            ]
            target = state.choose(owner, f"play_from_discard:{owner}", options)
            if target is not None:
                put_character_into_play_free(state, target)
        elif effect == "next_character_discount":
            state.next_character_discount += value
        elif effect == "bottom_cheap_opposing":
            targets = [
                target for target in list(state.characters_in_play(opponent))
                if (state.card_of(target).strength or 0) <= value
            ]
            for target in targets:
                state.move_zone(target, "deck")
        elif effect in ("damage_equal_own_characters", "damage_named_substitution"):
            if effect == "damage_equal_own_characters":
                amount = len(state.characters_in_play(owner))
                opposing_only = False
            else:
                normal_amount, named_amount, named_card = value
                has_named = any(state.card_of(target).name == named_card for target in state.characters_in_play(owner))
                amount = named_amount if has_named else normal_amount
                opposing_only = True
            targets = state.characters_in_play(opponent) if opposing_only else (state.characters_in_play(0) + state.characters_in_play(1))
            target = state.choose(owner, f"action_damage_target:{owner}:{amount}", targets)
            if target is not None:
                deal_damage(state, target, amount)
        elif effect == "temp_challenger_subtype":
            subtype, amount = value
            for target in state.characters_in_play(owner):
                card = state.card_of(target)
                if subtype in {part.lower() for part in card.subtypes}:
                    state.temp_challenger_bonus[target] = state.temp_challenger_bonus.get(target, 0) + amount
        elif effect == "damage":
            amount, opposing_only, damaged_only = value
            targets = state.characters_in_play(opponent) if opposing_only else (
                state.characters_in_play(0) + state.characters_in_play(1)
            )
            if damaged_only:
                targets = [target for target in targets if state.instances[target].damage > 0]
            target = state.choose(owner, f"action_damage_target:{owner}:{amount}", targets)
            if target is not None:
                deal_damage(state, target, amount)
        elif effect == "exert":
            opposing_only = value
            targets = state.characters_in_play(opponent) if opposing_only else (
                state.characters_in_play(0) + state.characters_in_play(1)
            )
            target = state.choose(owner, f"action_exert_target:{owner}", targets)
            if target is not None:
                state.instances[target].exerted = True
        else:
            raise ValueError(f"Unknown generic action effect: {effect}")


def _do_play_action(state: GameState, action: Action) -> None:
    p = state.active_player()
    iid = action["hand_iid"]
    card = state.card_of(iid)

    if action["pay"] == "ink":
        _exert_ink(state, p, card.cost, action.get("ink_drops", 0))
    elif action["pay"] == "sing":
        state.instances[action["singers"][0]].exerted = True
    elif action["pay"] == "sing_together":
        for sid in action["singers"]:
            state.instances[sid].exerted = True

    # Actions enter play, resolve, then go to discard (4.3.3.2).
    handler = ACTION_EFFECT_HANDLERS.get(card.full_name) or ACTION_EFFECT_HANDLERS.get(card.name)
    generic_plan = None if handler else _generic_action_plan(card)
    implemented = bool(handler) or generic_plan is not None
    suffix = "" if implemented else " (effect not implemented — no-op)"
    state.log_event(f"Played action {card.full_name}{suffix}")
    _fire_ally_broadcast(state, "on_play_action", iid)
    if handler:
        handler(state, iid)
    elif generic_plan is not None:
        _resolve_generic_action_effect(state, iid, generic_plan)
    state.move_zone(iid, "discard")

def _do_quest(state: GameState, action: Action) -> None:
    iid = action["iid"]
    card = state.card_of(iid)
    inst = state.instances[iid]
    inst.exerted = True
    p = state.player_of(iid)
    gained = _effective_lore(state, iid)
    p.lore += gained
    state.log_event(f"{card.full_name} quests for {gained} lore (total {p.lore})")

    if _has_kw(card, "Support"):
        _queue_trigger(state, iid, _resolve_support, "support")

    _fire_self(state, "on_quest", iid)
    _fire_ally_broadcast(state, "on_ally_quest", iid)


def _resolve_support(state: GameState, source_iid: int) -> None:
    """Support (8.13): "Whenever this character quests, you may add this
    character's Strength to another chosen character's Strength this turn."
    """
    owner_idx = state.instances[source_iid].owner
    others = [iid for iid in state.characters_in_play(owner_idx) if iid != source_iid]
    if not others:
        return
    choice = state.choose(owner_idx, "support_target", [None] + others)
    if choice is None:
        return
    card = state.card_of(source_iid)
    bonus = _effective_strength(state, source_iid, is_challenger=False)
    state.temp_strength_bonus[choice] = state.temp_strength_bonus.get(choice, 0) + bonus
    state.log_event(f"{card.full_name}'s Support gives {state.card_of(choice).full_name} +{bonus} Strength this turn")


def _do_challenge(state: GameState, action: Action) -> None:
    attacker_iid = action["attacker_iid"]
    defender_iid = action["defender_iid"]
    attacker = state.instances[attacker_iid]
    attacker_card = state.card_of(attacker_iid)
    defender_card = state.card_of(defender_iid)

    attacker.exerted = True
    if state.instances[attacker_iid].owner == state.active_idx:
        state.active_player_challenged_this_turn = True
    state.log_event(f"{attacker_card.full_name} challenges {defender_card.full_name}")

    # Challenge Declaration step: declaration triggers resolve before damage.
    _fire_self(state, "on_challenge", attacker_iid)
    _fire_self(state, "on_being_challenged", defender_iid)
    _fire_ally_broadcast(state, "on_ally_challenge", attacker_iid)
    _game_state_check(state, in_challenge=True)
    _resolve_trigger_bag(state, in_challenge=True)
    if state.instances[attacker_iid].zone != "play" or state.instances[defender_iid].zone != "play":
        return

    # Challenge Damage step: all challenge damage is dealt simultaneously.
    atk_str = _effective_strength(state, attacker_iid, is_challenger=True)
    if action["target_type"] == "character":
        def_str = _effective_strength(state, defender_iid, is_challenger=False)
        dmg_to_defender = _damage_after_resist(state, defender_iid, atk_str)
        dmg_to_attacker = _damage_after_resist(state, attacker_iid, def_str)
        state.instances[defender_iid].damage += dmg_to_defender
        state.instances[attacker_iid].damage += dmg_to_attacker
    else:  # location: doesn't deal damage back
        dmg_to_defender = _damage_after_resist(state, defender_iid, atk_str)
        state.instances[defender_iid].damage += dmg_to_defender
    _game_state_check(state, in_challenge=True)
    _resolve_trigger_bag(state, in_challenge=True)


def _do_move(state: GameState, action: Action) -> None:
    p = state.active_player()
    cost = action["cost"]
    if cost:
        _exert_ink(state, p, cost, action.get("ink_drops", 0))
    char_iid = action["char_iid"]
    location_iid = action["location_iid"]
    state.instances[char_iid].at_location = location_iid
    state.log_event(f"Moved {state.card_of(char_iid).full_name} to {state.card_of(location_iid).full_name}")
    _queue_registered_trigger(state, char_iid, "on_move", location_iid=location_iid)
    _queue_registered_trigger(state, location_iid, "on_character_moves_here", source_iid=char_iid)


def _do_end_turn(state: GameState, action: Action) -> None:
    state.log_event("Ends turn")
    _run_end_of_turn(state)


def _fire_self(state: GameState, event: str, iid: int, **ctx) -> None:
    _queue_registered_trigger(state, iid, event, **ctx)


def _fire_ally_broadcast(state: GameState, event: str, source_iid: int) -> None:
    owner = state.instances[source_iid].owner
    for listener_iid in _permanents_in_play(state, owner):
        if listener_iid == source_iid:
            continue
        _queue_registered_trigger(state, listener_iid, event, source_iid=source_iid)
