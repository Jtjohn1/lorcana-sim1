"""Game engine: setup, turn structure, legal actions, and applying them.

Scope (see README for the full list of gaps): this implements the core
turn structure and the mechanically well-defined keywords (Rush, Evasive,
Alert, Bodyguard, Ward-as-flag, Challenger, Resist, Reckless, Support,
base/Classification/Universal Shift, Singer, Sing Together, Boost) plus a
generic activated-ability system and a growing library of named
triggered/static abilities (see `lorcana/card_effects.py`). It does NOT
implement every one of the ~1,294 cards' unique text — see README "Known
gaps" for what's still missing. The registries below
(`TRIGGERED_ABILITY_HANDLERS`, `ACTION_EFFECT_HANDLERS`,
`ACTIVATED_ABILITY_HANDLERS`, `STATIC_READY_BLOCKERS`,
`STATIC_LORE_MODIFIERS`, `STATIC_DAMAGE_SOURCE_OVERRIDE`,
`STATIC_TURN_START_DISCOUNT`) are where card-specific text plugs in
without touching the turn-structure code.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .cards import Card, CardDatabase
from .state import GameState, GameConfig, PlayerState, CardInstance

Action = dict[str, Any]  # {"kind": "...", ...fields}. See README for the action catalog.

# --- Extension points for card-specific text --------------------------------
#
# TRIGGERED_ABILITY_HANDLERS: keyed by (card_full_name, event). `event` is one
#   of "on_play", "on_quest", "on_challenge", "on_ally_quest" (fires on each
#   of the SAME controller's OTHER characters whenever one of them quests —
#   e.g. Mickey Mouse's Secret Path), "on_end_of_turn" (fires once per
#   applicable card at its controller's end of turn — e.g. Elinor). Handler
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
STATIC_READY_BLOCKERS: dict[str, Callable] = {}
STATIC_LORE_MODIFIERS: dict[str, Callable] = {}
STATIC_DAMAGE_SOURCE_OVERRIDE: dict[str, str] = {}
STATIC_TURN_START_DISCOUNT: dict[str, int] = {}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def new_game(
    db: CardDatabase,
    deck_a_card_ids: list[int],
    deck_b_card_ids: list[int],
    config: GameConfig | None = None,
    seed: int | None = None,
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
    state.turn_number = 1

    for pidx in range(2):
        for _ in range(config.starting_hand_size):
            _draw_card(state, pidx)
    # NOTE: no mulligan step yet (config.allow_mulligan is a placeholder) —
    # see README "Known gaps".

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


def banish(state: GameState, iid: int) -> None:
    """Public alias of `_banish` for card_effects.py."""
    _banish(state, iid)


def deal_damage(state: GameState, iid: int, amount: int) -> None:
    if amount > 0:
        state.instances[iid].damage += amount


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
    fires that character's own on_play trigger. Doesn't support Shift or
    Bodyguard's enter-exerted choice — not needed by any card scripted so
    far; extend here if a future card needs it."""
    card = state.card_of(hand_iid)
    state.move_zone(hand_iid, "play")
    inst = state.instances[hand_iid]
    inst.entered_play_turn = state.turn_number
    inst.exerted = False
    state.log_event(f"Played {card.full_name} for free")
    _fire_self(state, "on_play", hand_iid, used_shift=False)
    return hand_iid


# ---------------------------------------------------------------------------
# Turn structure
# ---------------------------------------------------------------------------

def _run_start_of_turn(state: GameState, skip_draw: bool = False) -> None:
    p = state.active_player()
    pidx = state.active_idx

    # Per-turn trackers reset now.
    state.active_player_challenged_this_turn = False
    state.boost_used_this_turn = set()
    if state.global_no_challenge_until_owner == pidx:
        state.global_no_challenge_until_owner = None
    state.delayed_strength_mods = [m for m in state.delayed_strength_mods if m["owner"] != pidx]

    # Ready step (skip characters blocked by a registered STATIC_READY_BLOCKER,
    # e.g. Demona - Scourge of the Wyvern Clan's "Stone by Day").
    for iid in list(p.play) + list(p.inkwell):
        card = state.card_of(iid)
        blocker = STATIC_READY_BLOCKERS.get(card.full_name)
        if blocker and blocker(state, iid):
            state.log_event(f"{card.full_name} doesn't ready")
            continue
        state.instances[iid].exerted = False

    # Set step: characters played on a previous turn are now dry (handled by
    # comparing entered_play_turn < turn_number elsewhere); locations with a
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

    # "At the start of your turn" triggers (e.g. Mrs. Incredible - Super Stretchy).
    for iid in list(state.characters_in_play(pidx)):
        card = state.card_of(iid)
        handler = TRIGGERED_ABILITY_HANDLERS.get((card.full_name, "on_start_of_turn"))
        if handler:
            handler(state, iid)

    # Draw step
    if not skip_draw:
        if not p.deck:
            state.log_event(f"Player {pidx} has no cards to draw")
        else:
            _draw_card(state, pidx)
    p.inked_this_turn = False
    state.phase = "main"


def _run_end_of_turn(state: GameState) -> None:
    state.phase = "end_of_turn"
    pidx = state.active_idx

    # "At the end of your turn" triggers (e.g. Elinor - Renowned Diplomat).
    for iid in list(state.characters_in_play(pidx)):
        card = state.card_of(iid)
        handler = TRIGGERED_ABILITY_HANDLERS.get((card.full_name, "on_end_of_turn"))
        if handler:
            handler(state, iid)
    _game_state_check(state)

    # "This turn" effects (Comprehensive Rules 6.1.13.4) expire now, at the
    # active player's End-of-Turn Phase.
    state.temp_strength_bonus.clear()
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


def _check_banishments(state: GameState) -> None:
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
            _banish(state, iid)


def _banish(state: GameState, iid: int) -> None:
    inst = state.instances[iid]
    # Shift stacks: banishing the top card sends the whole stack to discard
    # (see Comprehensive Rules 8.10.7); we don't yet re-surface the
    # underneath card as "in play" since that's not how Shift works anyway.
    stack = [iid] + list(inst.stacked_under)
    # Cards tucked "under" this character (Boost) go to discard too.
    under_cards = [uid for uid, uinst in state.instances.items() if uinst.boosted_under == iid]
    for sid in stack + under_cards:
        state.move_zone(sid, "discard")


def _game_state_check(state: GameState) -> None:
    _check_banishments(state)
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
    strength += state.temp_strength_bonus.get(iid, 0)
    strength += sum(m["amount"] for m in state.delayed_strength_mods if m["iid"] == iid)
    if is_challenger and _has_kw(card, "Challenger"):
        strength += card.keyword_value("Challenger") or 0
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
    if _has_kw(card, "Resist"):
        incoming = max(0, incoming - (card.keyword_value("Resist") or 0))
    return incoming


def _shift_target_ids(state: GameState, player_idx: int, shift_card: Card) -> list[int]:
    """Instance ids this card could Shift onto, given its own Shift-family keyword."""
    targets = []
    my_chars = state.characters_in_play(player_idx)
    UNSUPPORTED = ("Duo Shift", "Combo Shift", "Temporary Shift", "Potato Shift")
    for kw in shift_card.keyword_abilities:
        if kw in UNSUPPORTED:
            continue
        if kw == "Shift":
            targets += [iid for iid in my_chars if state.card_of(iid).name == shift_card.name]
        elif kw == "Universal Shift":
            targets += my_chars
        elif kw.endswith(" Shift"):
            classification = kw[: -len(" Shift")]
            targets += [iid for iid in my_chars if classification in state.card_of(iid).subtypes]
    seen = set()
    out = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _has_unsupported_shift(card: Card) -> bool:
    return any(k in ("Duo Shift", "Combo Shift", "Temporary Shift", "Potato Shift") for k in card.keyword_abilities)


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


def _activated_ability_actions(state: GameState, iid: int, ready_ink_count: int) -> list[Action]:
    card = state.card_of(iid)
    inst = state.instances[iid]
    out = []
    for ability in card.abilities:
        if ability.type != "activated" or not ability.name:
            continue
        if ability.name not in ACTIVATED_ABILITY_HANDLERS:
            continue
        parsed = _parse_activated_costs(ability.costs)
        if parsed is None:
            continue
        if parsed["exert_self"]:
            if inst.exerted:
                continue
            if card.type == "Character" and not state.is_dry(iid):
                continue  # 4.4.2: exert-cost activated abilities need a dry character
        if parsed["ink"] > ready_ink_count:
            continue
        out.append({
            "kind": "activate_ability", "iid": iid, "ability_name": ability.name,
            "exert_self": parsed["exert_self"], "ink": parsed["ink"], "banish_self": parsed["banish_self"],
        })
    return out


def _do_activate_ability(state: GameState, action: Action) -> None:
    iid = action["iid"]
    card = state.card_of(iid)
    p = state.player_of(iid)
    if action["exert_self"]:
        state.instances[iid].exerted = True
    if action["ink"]:
        _exert_ink(state, p, action["ink"])
    state.log_event(f"{card.full_name} activates {action['ability_name']}")
    if action["banish_self"]:
        _banish(state, iid)
    handler = ACTIVATED_ABILITY_HANDLERS[action["ability_name"]]
    handler(state, iid)


# --- Boost -------------------------------------------------------------------

def _boost_actions(state: GameState, iid: int, ready_ink_count: int) -> list[Action]:
    card = state.card_of(iid)
    if not _has_kw(card, "Boost"):
        return []
    if iid in state.boost_used_this_turn:
        return []
    cost = card.keyword_value("Boost") or 0
    if cost > ready_ink_count:
        return []
    return [{"kind": "boost", "iid": iid, "cost": cost}]


def _do_boost(state: GameState, action: Action) -> None:
    iid = action["iid"]
    card = state.card_of(iid)
    p = state.player_of(iid)
    _exert_ink(state, p, action["cost"])
    state.boost_used_this_turn.add(iid)
    if not p.deck:
        state.log_event(f"{card.full_name} boosts but the deck is empty")
        return
    top_iid = p.deck[0]
    state.move_zone(top_iid, "under")
    state.instances[top_iid].boosted_under = iid
    state.log_event(f"{card.full_name} boosts (puts a card under itself)")
    handler = TRIGGERED_ABILITY_HANDLERS.get((card.full_name, "on_boost"))
    if handler:
        handler(state, iid)


# ---------------------------------------------------------------------------
# Legal actions
# ---------------------------------------------------------------------------

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
    discount = state.next_character_discount

    # Play cards from hand
    for iid in p.hand:
        card = state.card_of(iid)
        if card.type == "Character":
            effective_cost = max(0, card.cost - discount)
            if effective_cost <= ready_ink_count:
                actions.append({"kind": "play_character", "hand_iid": iid, "pay": "ink"})
                if _may_enter_exerted(card):
                    actions.append({"kind": "play_character", "hand_iid": iid, "pay": "ink", "enter_exerted": True})
            if not _has_unsupported_shift(card):
                for target_iid in _shift_target_ids(state, state.active_idx, card):
                    shift_cost = _shift_cost_for(card)
                    if shift_cost is not None and shift_cost <= ready_ink_count:
                        actions.append({
                            "kind": "play_character", "hand_iid": iid, "pay": "shift",
                            "shift_cost": shift_cost, "target_iid": target_iid,
                        })
        elif card.type == "Item":
            if card.cost <= ready_ink_count:
                actions.append({"kind": "play_item", "hand_iid": iid})
        elif card.type == "Location":
            if card.cost <= ready_ink_count:
                actions.append({"kind": "play_location", "hand_iid": iid})
        elif card.type == "Action":
            is_song = "Song" in card.subtypes
            if card.cost <= ready_ink_count:
                actions.append({"kind": "play_action", "hand_iid": iid, "pay": "ink"})
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
        if move_cost > ready_ink_count:
            continue
        for char_iid in state.characters_in_play(state.active_idx):
            if state.instances[char_iid].at_location != loc_iid:
                actions.append({"kind": "move", "char_iid": char_iid, "location_iid": loc_iid, "cost": move_cost})

    # Activated abilities and Boost, for every card the active player controls in play
    for iid in p.play:
        actions += _activated_ability_actions(state, iid, ready_ink_count)
        actions += _boost_actions(state, iid, ready_ink_count)

    # End turn — blocked if a ready Reckless character could still challenge (8.7.3)
    if not _must_challenge_with_reckless(state):
        actions.append({"kind": "end_turn"})

    return actions


def _shift_cost_for(card: Card) -> int | None:
    for ability in card.abilities:
        if ability.type == "keyword" and ability.keyword and (
            ability.keyword == "Shift" or ability.keyword.endswith("Shift")
        ) and ability.keyword not in ("Duo Shift", "Combo Shift", "Temporary Shift", "Potato Shift"):
            return ability.keyword_value_number
    return None


def _song_alt_cost_actions(state: GameState, song_iid: int, song_card: Card) -> list[Action]:
    """Comprehensive Rules 5.4.4.2: ANY character with ink cost >= the song's
    cost can exert to sing it for free — this is a universal rule for all
    Song cards, not something gated behind the Singer keyword. Singer N
    additionally lets a character count as cost N for singing purposes
    (see 8.11), and Sing Together N lets a group's total cost meet the bar."""
    out = []
    my_chars = [iid for iid in state.characters_in_play(state.active_idx) if state.is_dry(iid) and not state.instances[iid].exerted]

    for iid in my_chars:
        c = state.card_of(iid)
        singing_cost = c.keyword_value("Singer") if _has_kw(c, "Singer") else c.cost
        if singing_cost is not None and singing_cost >= song_card.cost:
            out.append({"kind": "play_action", "hand_iid": song_iid, "pay": "sing", "singers": [iid]})

    sing_together_n = None
    for ability in song_card.abilities:
        if ability.keyword == "Sing Together":
            sing_together_n = ability.keyword_value_number
    if sing_together_n is not None and my_chars:
        total_cost = sum(state.card_of(iid).cost for iid in my_chars)
        if total_cost >= sing_together_n:
            # MVP simplification: offer "sing with everyone available" rather than
            # enumerating every valid subset (which is combinatorial). See README.
            out.append({"kind": "play_action", "hand_iid": song_iid, "pay": "sing_together", "singers": list(my_chars)})

    return out


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

def apply_action(state: GameState, action: Action) -> None:
    if state.winner is not None:
        return
    kind = action["kind"]
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


def _exert_ink(state: GameState, p: PlayerState, amount: int) -> None:
    ready = [iid for iid in p.inkwell if not state.instances[iid].exerted]
    if len(ready) < amount:
        raise ValueError("Not enough ready ink")
    for iid in ready[:amount]:
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
        cost = max(0, card.cost - state.next_character_discount)
        _exert_ink(state, p, cost)
        state.next_character_discount = 0  # consumed, regardless of amount used
    else:
        _exert_ink(state, p, action["shift_cost"])

    state.move_zone(iid, "play")
    inst = state.instances[iid]
    inst.entered_play_turn = state.turn_number
    inst.exerted = False

    if used_shift:
        target_iid = action["target_iid"]
        target = state.instances[target_iid]
        inst.stacked_under = [target_iid] + target.stacked_under
        target.stacked_on = iid
        # Inherit dryness/ready state from the character shifted onto (8.10.2/8.10.4)
        inst.exerted = target.exerted
        inst.entered_play_turn = target.entered_play_turn
        inst.damage = target.damage
        state.log_event(f"Shifted {card.full_name} onto {state.card_of(target_iid).full_name}")
    else:
        if _may_enter_exerted(card) and action.get("enter_exerted"):
            inst.exerted = True
        state.log_event(f"Played {card.full_name}")

    _fire_self(state, "on_play", iid, used_shift=used_shift)


def _do_play_item(state: GameState, action: Action) -> None:
    p = state.active_player()
    iid = action["hand_iid"]
    card = state.card_of(iid)
    _exert_ink(state, p, card.cost)
    state.move_zone(iid, "play")
    state.instances[iid].entered_play_turn = state.turn_number
    state.log_event(f"Played item {card.full_name}")
    handler = TRIGGERED_ABILITY_HANDLERS.get((card.full_name, "on_play_item"))
    if handler:
        handler(state, iid)


def _do_play_location(state: GameState, action: Action) -> None:
    p = state.active_player()
    iid = action["hand_iid"]
    card = state.card_of(iid)
    _exert_ink(state, p, card.cost)
    state.move_zone(iid, "play")
    state.instances[iid].entered_play_turn = state.turn_number
    state.log_event(f"Played location {card.full_name}")
    handler = TRIGGERED_ABILITY_HANDLERS.get((card.full_name, "on_play_location"))
    if handler:
        handler(state, iid)


def _do_play_action(state: GameState, action: Action) -> None:
    p = state.active_player()
    iid = action["hand_iid"]
    card = state.card_of(iid)

    if action["pay"] == "ink":
        _exert_ink(state, p, card.cost)
    elif action["pay"] == "sing":
        state.instances[action["singers"][0]].exerted = True
    elif action["pay"] == "sing_together":
        for sid in action["singers"]:
            state.instances[sid].exerted = True

    # Actions enter play, resolve, then go to discard (4.3.3.2).
    handler = ACTION_EFFECT_HANDLERS.get(card.full_name) or ACTION_EFFECT_HANDLERS.get(card.name)
    state.log_event(f"Played action {card.full_name}" + ("" if handler else " (effect not implemented — no-op)"))
    if handler:
        handler(state, iid)
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
        _resolve_support(state, iid)

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
    bonus = card.strength or 0
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

    atk_str = _effective_strength(state, attacker_iid, is_challenger=True)
    if action["target_type"] == "character":
        def_str = _effective_strength(state, defender_iid, is_challenger=False)
        dmg_to_defender = _damage_after_resist(state, defender_iid, atk_str)
        dmg_to_attacker = _damage_after_resist(state, attacker_iid, def_str)
        state.instances[defender_iid].damage += dmg_to_defender
        attacker.damage += dmg_to_attacker
    else:  # location: doesn't deal damage back
        dmg_to_defender = _damage_after_resist(state, defender_iid, atk_str)
        state.instances[defender_iid].damage += dmg_to_defender

    _fire_self(state, "on_challenge", attacker_iid)


def _do_move(state: GameState, action: Action) -> None:
    p = state.active_player()
    cost = action["cost"]
    if cost:
        _exert_ink(state, p, cost)
    state.instances[action["char_iid"]].at_location = action["location_iid"]
    state.log_event(f"Moved {state.card_of(action['char_iid']).full_name} to {state.card_of(action['location_iid']).full_name}")


def _do_end_turn(state: GameState, action: Action) -> None:
    state.log_event("Ends turn")
    _run_end_of_turn(state)


def _fire_self(state: GameState, event: str, iid: int, **ctx) -> None:
    card = state.card_of(iid)
    handler = TRIGGERED_ABILITY_HANDLERS.get((card.full_name, event)) or TRIGGERED_ABILITY_HANDLERS.get((card.name, event))
    if handler:
        handler(state, iid, **ctx)


def _fire_ally_broadcast(state: GameState, event: str, source_iid: int) -> None:
    owner = state.instances[source_iid].owner
    for listener_iid in state.characters_in_play(owner):
        if listener_iid == source_iid:
            continue
        card = state.card_of(listener_iid)
        handler = TRIGGERED_ABILITY_HANDLERS.get((card.full_name, event))
        if handler:
            handler(state, listener_iid, source_iid=source_iid)
