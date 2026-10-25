"""Card-specific effect implementations, registered into engine.py's
extension-point dictionaries.

Scope: named handlers for card text that is not resolved by the engine's
generic keyword and text templates. This is a growing subset of the full
sets 9–14 database; `check_card_coverage.py` reports the cards that still
need resolvers. Import this module (it's imported by `lorcana/__init__.py`)
to register everything below; nothing here runs until the engine calls into
the registries.

A few cards' printed text was genuinely ambiguous when read literally out
of the LorcanaJSON extraction; where that happened, the interpretation
used is noted in a comment on that handler. If you have the physical
card/official ruling and it reads differently, that's the place to fix.

Pattern to copy for a new card: pick the right registry from engine.py's
module docstring, write a small handler function, register it in
`register()` at the bottom.
"""
from __future__ import annotations

from . import engine as E
from .state import GameState


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _reveal_hand_and_discard_chooser_picks(state: GameState, chooser_idx: int, target_idx: int, filter_fn) -> int | None:
    """"Chosen opponent reveals their hand and discards a [X] card OF YOUR
    CHOICE" — the acting player (chooser_idx) picks which of the target's
    matching hand cards gets discarded."""
    options = [iid for iid in state.players[target_idx].hand if filter_fn(state.card_of(iid))]
    if not options:
        return None
    choice = state.choose(chooser_idx, "discard_choice", options)
    state.move_zone(choice, "discard")
    return choice


def _reveal_hand_and_discard_target_picks(state: GameState, target_idx: int, filter_fn) -> int | None:
    """"Chosen opponent reveals their hand and discards a [X] card OF THEIR
    CHOICE" — the target player picks which of their own matching cards to
    discard."""
    options = [iid for iid in state.players[target_idx].hand if filter_fn(state.card_of(iid))]
    if not options:
        return None
    choice = state.choose(target_idx, "discard_choice", options)
    state.move_zone(choice, "discard")
    return choice


def _is_character(card) -> bool:
    return card.type == "Character"


def _is_song(card) -> bool:
    return "Song" in card.subtypes


def _look_top_and_maybe_take(state: GameState, pidx: int, n: int, filter_fn) -> None:
    """Look at the top n cards of pidx's deck; may take one matching
    filter_fn into hand; the rest go to the bottom in their original
    relative order (the engine doesn't model reordering choices for "any
    order" text — see README)."""
    p = state.players[pidx]
    peek = list(p.deck[:n])
    if not peek:
        return
    matches = [iid for iid in peek if filter_fn(state.card_of(iid))]
    choice = state.choose(pidx, "look_and_take", [None] + matches) if matches else None
    rest = [iid for iid in peek if iid != choice]
    if choice is not None:
        state.move_zone(choice, "hand")
        state.log_event(f"Took {state.card_of(choice).full_name} off the top {n}")
    for iid in rest:
        p.deck.remove(iid)
        p.deck.append(iid)  # bottom of deck


# ---------------------------------------------------------------------------
# Deck 1 cards
# ---------------------------------------------------------------------------

# Agustin Madrigal - Exceptionally Kind: Support only — generic, no handler needed.
# Eilonwy - Princess of Llyr: Support only — generic, no handler needed.
# Luisa Madrigal - Pushing Through: Challenger +2 only — generic, no handler needed.
# Sven - Leaping Reindeer: Rush/Challenger/Evasive only — generic, no handler needed.
# The Queen - Regal Monarch, Thomas - Wide-Eyed Recruit: vanilla, no text at all.
# David - Protective Snowboarder: Bodyguard only — generic, no handler needed.

def _cant_hold_it_back_anymore(state: GameState, iid: int) -> None:
    """Action (Song), cost 4: "Exert chosen opposing character. Move all
    damage counters from all other characters to that character." """
    card = state.card_of(iid)
    caster = state.instances[iid].owner
    opp = state.opponent_idx(caster)
    opp_chars = state.characters_in_play(opp)
    if not opp_chars:
        return
    target = state.choose(caster, "exert_and_pile_damage_target", opp_chars)
    state.instances[target].exerted = True
    total_moved = 0
    for other_iid, other_inst in list(state.instances.items()):
        if other_iid == target or other_inst.zone != "play":
            continue
        total_moved += E.move_damage(state, other_iid, target, other_inst.damage)
    state.log_event(f"{card.full_name} exerts {state.card_of(target).full_name} and piles {total_moved} damage onto it")


def _cheshire_cat_on_boost(state: GameState, iid: int) -> None:
    """IT'S LOADS OF FUN: whenever you put a card under this character (via
    Boost), you may move up to 2 damage counters from a chosen character to
    a chosen opposing character. Simplification: always moves min(2,
    available damage) rather than offering a 0/1/2 amount choice — see
    README."""
    card = state.card_of(iid)
    caster = state.instances[iid].owner
    opp = state.opponent_idx(caster)
    damaged = [i for i, inst in state.instances.items() if inst.zone == "play" and inst.damage > 0]
    if not damaged:
        return
    source = state.choose(caster, "move_damage_source", [None] + damaged)
    if source is None:
        return
    opp_chars = state.characters_in_play(opp)
    if not opp_chars:
        return
    dest = state.choose(caster, "move_damage_dest", opp_chars)
    moved = E.move_damage(state, source, dest, 2)
    state.log_event(f"{card.full_name}'s Loads of Fun moves {moved} damage to {state.card_of(dest).full_name}")


def _dale_spike_suit_register():
    E.STATIC_DAMAGE_SOURCE_OVERRIDE["Dale - Ready for His Shot"] = "willpower"


def _demona_on_play(state: GameState, iid: int, **ctx) -> None:
    """AD SAXUM COMMUTATE: exert all opposing characters, then each player
    with <3 cards in hand draws until they have 3."""
    card = state.card_of(iid)
    caster = state.instances[iid].owner
    opp = state.opponent_idx(caster)
    for oiid in state.characters_in_play(opp):
        state.instances[oiid].exerted = True
    for pidx in (0, 1):
        hand_size = len(state.players[pidx].hand)
        if hand_size < 3:
            E.draw_cards(state, pidx, 3 - hand_size)
    state.log_event(f"{card.full_name} exerts all opposing characters and tops both hands up to 3")


def _demona_stone_by_day(state: GameState, iid: int) -> bool:
    """STONE BY DAY: if you have 3+ cards in hand, this character can't ready."""
    owner = state.instances[iid].owner
    if _angela_suppresses_stone_by_day(state, iid):
        return False
    return len(state.players[owner].hand) >= 3


def _dumbo_breaking_records(state: GameState, iid: int) -> None:
    """Resolve either Dumbo activated ability: draw a card and gain 1 lore."""
    owner = state.instances[iid].owner
    E.draw_cards(state, owner, 1)
    state.players[owner].lore += 1
    state.log_event(f"{state.card_of(iid).full_name}'s activated ability draws a card and gains 1 lore")


def _dumbo_making_history_grant(state: GameState, source_iid: int, target_iid: int):
    if source_iid == target_iid or state.instances[target_iid].owner != state.instances[source_iid].owner:
        return []
    target = state.card_of(target_iid)
    if target.type != "Character" or not target.has_keyword("Evasive"):
        return []
    return [{"name": "MAKING HISTORY", "costs": ("⟳", "1 ⬡")}]


def _gaston_watch_this(state: GameState, iid: int, **ctx) -> None:
    """WATCH THIS!: may banish chosen character with 5+ Strength (either
    player's — the text doesn't say "opposing")."""
    card = state.card_of(iid)
    caster = state.instances[iid].owner
    candidates = [
        i for i in list(state.characters_in_play(0)) + list(state.characters_in_play(1))
        if (state.card_of(i).strength or 0) >= 5
    ]
    if not candidates:
        return
    choice = state.choose(caster, "banish_5plus_strength", [None] + candidates)
    if choice is None:
        return
    state.log_event(f"{card.full_name} banishes {state.card_of(choice).full_name} (Watch This!)")
    E.banish(state, choice)


def _grandmother_willow_register():
    E.STATIC_TURN_START_DISCOUNT["Grandmother Willow - Ancient Advisor"] = 1


def _hades_what_dya_say(state: GameState, iid: int, **ctx) -> None:
    """WHAT D'YA SAY?: may choose an opposing character. If you do, draw 2
    cards unless that character's player puts that card on the bottom of
    their deck.

    Interpretation note: read literally ("that card") this is ambiguous —
    a bottomed *card* doesn't obviously connect to a chosen *character*.
    Given the card's "making a deal" flavor, this is implemented as: the
    opposing player chooses between (a) bottoming the chosen character
    (losing it, but denying the draw) or (b) letting Hades' controller
    draw 2 cards. Revisit if you have the official ruling/rules text.
    """
    card = state.card_of(iid)
    caster = state.instances[iid].owner
    opp = state.opponent_idx(caster)
    opp_chars = state.characters_in_play(opp)
    if not opp_chars:
        return
    target = state.choose(caster, "hades_target", [None] + opp_chars)
    if target is None:
        return
    decision = state.choose(opp, "hades_deal", ["bottom_character", "allow_draw"])
    if decision == "bottom_character":
        state.move_zone(target, "deck")  # move_zone appends to the end of the deck list, i.e. the bottom
        state.log_event(f"{state.card_of(target).full_name}'s controller bottoms it to deny the deal")
    else:
        E.draw_cards(state, caster, 2)
        state.log_event(f"{card.full_name}'s deal goes through — draws 2 cards")


def _hamm_loose_change(state: GameState, iid: int) -> None:
    owner = state.instances[iid].owner
    state.next_character_discount += 1
    state.log_event(f"{state.card_of(iid).full_name}'s Loose Change: next character costs 1 less")


def _isis_chill_out(state: GameState, iid: int, **ctx) -> None:
    """CHILL OUT: exert chosen opposing character."""
    caster = state.instances[iid].owner
    opp = state.opponent_idx(caster)
    opp_chars = state.characters_in_play(opp)
    if not opp_chars:
        return
    target = state.choose(caster, "exert_target", opp_chars)
    state.instances[target].exerted = True
    state.log_event(f"{state.card_of(iid).full_name} exerts {state.card_of(target).full_name}")


def _junior_woodchuck_guidebook(state: GameState, iid: int) -> None:
    owner = state.instances[iid].owner
    E.draw_cards(state, owner, 2)
    state.log_event("Junior Woodchuck Guidebook draws 2 cards")


def _lantern_birthday_lights(state: GameState, iid: int) -> None:
    state.next_character_discount += 1
    state.log_event("Lantern's Birthday Lights: next character costs 1 less")


def _mrs_incredible_flexible_thinking(state: GameState, iid: int) -> None:
    """FLEXIBLE THINKING: at the start of your turn, choose one:
    (a) this character gains Evasive until the start of your next turn, or
    (b) this character gets +1 Lore this turn.
    Simplification: only branch (b) is implemented — granting a temporary
    keyword (Evasive) needs a per-instance temporary-keyword mechanism that
    no other card here needs, so it's not built yet (see README). This
    handler always takes branch (b) rather than offering a choice between a
    real option and a fake one.
    """
    _grant_temp_lore_this_turn(state, iid, 1)
    state.log_event(f"{state.card_of(iid).full_name} gets +1 Lore this turn (Flexible Thinking)")


_temp_lore_bonus: dict[int, int] = {}


def _grant_temp_lore_this_turn(state: GameState, iid: int, amount: int) -> None:
    _temp_lore_bonus[iid] = _temp_lore_bonus.get(iid, 0) + amount


def _mrs_incredible_lore_modifier(state: GameState, iid: int) -> int:
    return _temp_lore_bonus.pop(iid, 0)  # one-shot: consumed the first time this character quests


def _mickey_expedition_leader_secret_path(state: GameState, listener_iid: int, source_iid: int, **ctx) -> None:
    """SECRET PATH: while this character (Mickey) is exerted, whenever one
    of your OTHER characters quests, chosen opposing character gets -2
    Strength until the start of your next turn."""
    if not state.instances[listener_iid].exerted:
        return
    owner = state.instances[listener_iid].owner
    opp = state.opponent_idx(owner)
    opp_chars = state.characters_in_play(opp)
    if not opp_chars:
        return
    target = state.choose(owner, "secret_path_target", opp_chars)
    state.delayed_strength_mods.append({"iid": target, "amount": -2, "owner": owner})
    state.log_event(f"{state.card_of(listener_iid).full_name}'s Secret Path: {state.card_of(target).full_name} gets -2 Strength until your next turn")


def _pluto_good_dog(state: GameState, iid: int) -> None:
    state.next_character_discount += 1
    state.log_event(f"{state.card_of(iid).full_name}'s Good Dog: next character costs 1 less")


def _pocahontas_stay_close(state: GameState, iid: int, **ctx) -> None:
    """STAY CLOSE: may play a character with cost 1 for free."""
    owner = state.instances[iid].owner
    cheap = [h for h in state.players[owner].hand if state.card_of(h).type == "Character" and state.card_of(h).cost == 1]
    if not cheap:
        return
    choice = state.choose(owner, "free_play_cost1", [None] + cheap)
    if choice is not None:
        E.put_character_into_play_free(state, choice)


def _pocahontas_peacekeeper_calming_words(state: GameState, iid: int, used_shift: bool = False, **ctx) -> None:
    """CALMING WORDS: if you used Shift to play her and none of your
    characters challenged this turn, characters can't challenge until the
    start of your next turn."""
    owner = state.instances[iid].owner
    if used_shift and not state.active_player_challenged_this_turn:
        state.global_no_challenge_until_owner = owner
        state.log_event(f"{state.card_of(iid).full_name}'s Calming Words: no one can challenge until your next turn")


def _sulley_rehire(state: GameState, iid: int, **ctx) -> None:
    """REHIRE: may return a character card from your discard to your hand."""
    owner = state.instances[iid].owner
    candidates = [d for d in state.players[owner].discard if state.card_of(d).type == "Character"]
    if not candidates:
        return
    choice = state.choose(owner, "rehire_target", [None] + candidates)
    if choice is not None:
        state.move_zone(choice, "hand")
        state.log_event(f"{state.card_of(iid).full_name}'s Rehire returns {state.card_of(choice).full_name} to hand")


def _stitch_ohana(state: GameState, iid: int, **ctx) -> None:
    """OHANA: if you have 2+ other characters in play, may draw 2 cards."""
    owner = state.instances[iid].owner
    others = [c for c in state.characters_in_play(owner) if c != iid]
    if len(others) < 2:
        return
    choice = state.choose(owner, "ohana_may_draw", [True, False])
    if choice:
        E.draw_cards(state, owner, 2)
        state.log_event(f"{state.card_of(iid).full_name}'s Ohana draws 2 cards")


def _worlds_greatest_criminal_mind(state: GameState, iid: int) -> None:
    """Banish chosen character with 5+ Strength (either player's)."""
    caster = state.instances[iid].owner
    candidates = [
        i for i in list(state.characters_in_play(0)) + list(state.characters_in_play(1))
        if (state.card_of(i).strength or 0) >= 5
    ]
    if not candidates:
        return
    choice = state.choose(caster, "banish_5plus_strength", candidates)
    state.log_event(f"World's Greatest Criminal Mind banishes {state.card_of(choice).full_name}")
    E.banish(state, choice)


def _second_star_to_the_right(state: GameState, iid: int) -> None:
    """Chosen player draws 5 cards."""
    caster = state.instances[iid].owner
    choice = state.choose(caster, "chosen_player_draws_5", [0, 1])
    E.draw_cards(state, choice, 5)
    state.log_event(f"Second Star to the Right: player {choice} draws 5")


def _ohana_means_family(state: GameState, iid: int) -> None:
    """Remove all damage from chosen character of yours. Draw a card for
    each 1 damage removed this way."""
    caster = state.instances[iid].owner
    my_damaged = [i for i in state.characters_in_play(caster) if state.instances[i].damage > 0]
    if not my_damaged:
        return
    target = state.choose(caster, "heal_target", my_damaged)
    removed = state.instances[target].damage
    state.instances[target].damage = 0
    E.draw_cards(state, caster, removed)
    state.log_event(f"Ohana Means Family heals {removed} off {state.card_of(target).full_name}, draws {removed}")


# ---------------------------------------------------------------------------
# Deck 2 cards
# ---------------------------------------------------------------------------

def _aurora_royal_welcome(state: GameState, iid: int, **ctx) -> None:
    """ROYAL WELCOME: whenever this character quests, pay 1 less for the
    next Princess or Queen character you play this turn.
    Simplification: implemented as a flat discount added to the shared
    `next_character_discount` pool (like other discount sources here)
    rather than one scoped only to Princess/Queen — see README. In
    practice this only over-applies if a non-Princess/Queen character is
    played first while the discount is still banked, which is a minor,
    documented approximation.
    """
    state.next_character_discount += 1
    state.log_event(f"{state.card_of(iid).full_name}'s Royal Welcome: next character costs 1 less")


def _bobby_so_cheesy(state: GameState, iid: int, **ctx) -> None:
    """SO CHEESY: may draw a card, then choose and discard a card."""
    owner = state.instances[iid].owner
    choice = state.choose(owner, "may_draw", [True, False])
    if not choice:
        return
    E.draw_cards(state, owner, 1)
    hand = state.players[owner].hand
    if not hand:
        return
    discard_choice = state.choose(owner, "discard_choice", hand)
    state.move_zone(discard_choice, "discard")
    state.log_event(f"{state.card_of(iid).full_name}'s So Cheesy: drew then discarded {state.card_of(discard_choice).full_name}")


def _elinor_coordinated_efforts(state: GameState, iid: int, **ctx) -> None:
    """COORDINATED EFFORTS: at the end of your turn, if you have 3+ exerted
    characters in play, deal 1 damage to chosen opposing character, gain 1
    lore, and draw a card."""
    owner = state.instances[iid].owner
    exerted_count = sum(1 for c in state.characters_in_play(owner) if state.instances[c].exerted)
    if exerted_count < 3:
        return
    opp = state.opponent_idx(owner)
    opp_chars = state.characters_in_play(opp)
    if opp_chars:
        target = state.choose(owner, "coordinated_efforts_target", opp_chars)
        E.deal_damage(state, target, 1)
    state.players[owner].lore += 1
    E.draw_cards(state, owner, 1)
    state.log_event(f"{state.card_of(iid).full_name}'s Coordinated Efforts triggers")


def _mike_find_a_friend(state: GameState, iid: int, **ctx) -> None:
    """FIND A FRIEND: on play AND whenever this character quests, each
    player reveals the top card of their deck; if it's a Character, they
    may put it into their hand, otherwise it goes to the bottom."""
    for pidx in (0, 1):
        deck = state.players[pidx].deck
        if not deck:
            continue
        top = deck[0]
        card = state.card_of(top)
        if card.type == "Character":
            keep = state.choose(pidx, "find_a_friend_keep", [True, False])
            if keep:
                state.move_zone(top, "hand")
                continue
        deck.remove(top)
        deck.append(top)
    state.log_event(f"{state.card_of(iid).full_name}'s Find a Friend resolves for both players")


def _chernabog_unatural_force(state: GameState, iid: int, **ctx) -> None:
    """UNNATURAL FORCE: shuffle an opposing character away; its player may play a Character."""
    owner = state.instances[iid].owner
    opponent = state.opponent_idx(owner)
    targets = state.characters_in_play(opponent)
    target = state.choose(owner, "chernabog_target", targets)
    if target is None:
        return
    affected_player = state.instances[target].owner
    state.move_zone(target, "deck")
    state.rng.shuffle(state.players[affected_player].deck)
    options = [
        card_iid for card_iid in state.players[affected_player].discard
        if state.card_of(card_iid).type == "Character"
    ]
    if not options or not state.choose(
        affected_player, f"chernabog_may_play:{affected_player}", [True, False]
    ):
        return
    chosen = state.choose(affected_player, f"play_from_discard:{affected_player}", options)
    if chosen is not None:
        E.put_character_into_play_free(state, chosen)


def _milo_getting_his_hands_dirty(state: GameState, iid: int, **ctx) -> None:
    """GETTING HIS HANDS DIRTY: discard a card to return a chosen Character."""
    owner = state.instances[iid].owner
    targets = [
        target for player_idx in (0, 1)
        for target in state.characters_in_play(player_idx)
        if not (
            state.instances[target].owner != owner
            and state.card_of(target).has_keyword("Ward")
        )
    ]
    if not targets or not state.players[owner].hand:
        return
    if not state.choose(owner, f"may_discard_to_bounce:{owner}", [True, False]):
        return
    hand = list(state.players[owner].hand)
    discarded = state.choose(owner, "discard_choice", hand)
    if discarded not in hand:
        return
    state.move_zone(discarded, "discard")
    targets = [
        target for player_idx in (0, 1)
        for target in state.characters_in_play(player_idx)
        if not (
            state.instances[target].owner != owner
            and state.card_of(target).has_keyword("Ward")
        )
    ]
    target = state.choose(owner, f"bounce_target:{owner}", targets)
    if target is not None:
        state.move_zone(target, "hand")


def _milo_discard_recovery(state: GameState, iid: int, **ctx) -> None:
    owner = state.instances[iid].owner
    if state.cards_discarded_this_turn.get(owner, 0) >= 2:
        E.draw_cards(state, owner, 1)
        state.log_event(f"{state.card_of(iid).full_name} draws after two cards entered your discard")


def _meilin_losing_control(state: GameState, iid: int, **ctx) -> None:
    """LOSING CONTROL: look at four; take a Red Panda character or Song."""
    owner = state.instances[iid].owner
    _look_top_and_maybe_take(
        state, owner, 4,
        lambda card: (
            card.type == "Character" and "Red Panda" in card.subtypes
        ) or (card.type == "Action" and "Song" in card.subtypes),
    )


def _mowgli_have_a_better_look(state: GameState, iid: int, **ctx) -> None:
    """HAVE A BETTER LOOK: chosen opponent reveals hand and discards a
    non-character card OF THEIR CHOICE."""
    caster = state.instances[iid].owner
    opp = state.opponent_idx(caster)
    _reveal_hand_and_discard_target_picks(state, opp, lambda c: not _is_character(c))
    state.log_event(f"{state.card_of(iid).full_name}'s Have a Better Look resolves")


def _nani_thats_your_cue(state: GameState, iid: int, **ctx) -> None:
    """THAT'S YOUR CUE: look at top 4, may take a cost<=2 character card,
    rest to the bottom."""
    owner = state.instances[iid].owner
    _look_top_and_maybe_take(state, owner, 4, lambda c: _is_character(c) and (c.cost or 0) <= 2)


def _queen_devious_disguise_evil_scheme(state: GameState, iid: int, **ctx) -> None:
    """EVIL SCHEME: may draw a card. If you do, each opponent gains 2 lore."""
    owner = state.instances[iid].owner
    choice = state.choose(owner, "evil_scheme_may_draw", [True, False])
    if choice:
        E.draw_cards(state, owner, 1)
        state.players[state.opponent_idx(owner)].lore += 2
        state.log_event(f"{state.card_of(iid).full_name}'s Evil Scheme: draws, opponent gains 2 lore")


def _queen_devious_disguise_jealous_heart(state: GameState, iid: int) -> int:
    owner = state.instances[iid].owner
    opp = state.opponent_idx(owner)
    if state.players[opp].lore > state.players[owner].lore:
        return 2
    return 0


def _mushu_tip_the_scales(state: GameState, iid: int, **ctx) -> None:
    """TIP THE SCALES: whenever this character quests, if an opponent has
    more cards in hand than you, may draw a card."""
    owner = state.instances[iid].owner
    opp = state.opponent_idx(owner)
    if len(state.players[opp].hand) > len(state.players[owner].hand):
        choice = state.choose(owner, "tip_the_scales_may_draw", [True, False])
        if choice:
            E.draw_cards(state, owner, 1)
            state.log_event(f"{state.card_of(iid).full_name}'s Tip the Scales draws a card")


def _ursula_deceiver_never_miss_it(state: GameState, iid: int, **ctx) -> None:
    """YOU'LL NEVER EVEN MISS IT: chosen opponent reveals hand and discards
    a song card OF YOUR CHOICE (the caster picks)."""
    caster = state.instances[iid].owner
    opp = state.opponent_idx(caster)
    _reveal_hand_and_discard_chooser_picks(state, caster, opp, _is_song)
    state.log_event(f"{state.card_of(iid).full_name}'s You'll Never Even Miss It resolves")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _aladdin_doing_his_part(state: GameState, iid: int, **ctx) -> None:
    owner = state.instances[iid].owner
    items = [target for pidx in (0, 1) for target in state.players[pidx].play
             if state.card_of(target).type == "Item" and state.instances[target].stacked_on is None]
    player = state.players[owner]
    ready_ink = sum(not state.instances[target].exerted for target in player.inkwell)
    if not items or ready_ink + player.ink_drops < 1:
        return
    if not state.choose(owner, "aladdin_may_pay", [False, True]):
        return
    drops = 1 if ready_ink == 0 else 0
    E._exert_ink(state, player, 1, drops)
    target = state.choose(owner, f"aladdin_banish_item:{owner}", items)
    if target is not None:
        E.banish(state, target)


def _angel_good_aim_legal(state: GameState, iid: int) -> bool:
    owner = state.instances[iid].owner
    if not state.players[owner].hand:
        return False
    for pidx in (0, 1):
        for target in state.characters_in_play(pidx):
            if pidx == owner or not state.card_of(target).has_keyword("Ward"):
                return True
    return False


def _angel_good_aim(state: GameState, iid: int) -> None:
    owner = state.instances[iid].owner
    discarded = state.choose(owner, "discard_choice", list(state.players[owner].hand))
    if discarded is None:
        return
    state.move_zone(discarded, "discard")
    targets = state.characters_in_play(0) + state.characters_in_play(1)
    target = state.choose(owner, f"action_damage_target:{owner}:2", targets)
    if target is not None:
        E.deal_damage(state, target, 2)


def _angel_night_warrior(state: GameState, iid: int, **ctx) -> None:
    owner = state.instances[iid].owner
    targets = state.characters_in_play(0) + state.characters_in_play(1)
    if not targets:
        return
    target = state.choose(owner, f"angela_challenger_resist_target:{owner}", [None] + targets)
    if target is None:
        return
    state.delayed_challenger_mods.append({"iid": target, "amount": 2, "owner": owner})
    state.delayed_resist_mods.append({"iid": target, "amount": 2, "owner": owner})


def _rapunzel_tower_defender(state: GameState, iid: int, **ctx) -> None:
    owner = state.instances[iid].owner
    hand = list(state.players[owner].hand)
    characters = state.characters_in_play(0) + state.characters_in_play(1)
    if not hand or not characters:
        return
    discard = state.choose(owner, f"rapunzel_discard:{owner}", [None] + hand)
    if discard is None:
        return
    state.move_zone(discard, "discard")
    target = state.choose(owner, f"bounce_target:{owner}", characters)
    if target is not None:
        state.move_zone(target, "hand")
        state.log_event(f"{state.card_of(iid).full_name} returns {state.card_of(target).full_name} to hand")


def _launchpad_what_did_you_need(state: GameState, iid: int) -> None:
    owner = state.instances[iid].owner
    E.draw_cards(state, owner, 1)
    has_darkwing = any(state.card_of(target).name == "Darkwing Duck" for target in state.characters_in_play(owner))
    if not has_darkwing and state.players[owner].hand:
        discarded = state.choose(owner, "discard_choice", list(state.players[owner].hand))
        if discarded is not None:
            state.move_zone(discarded, "discard")


def _angela_suppresses_stone_by_day(state: GameState, iid: int) -> bool:
    owner = state.instances[iid].owner
    demona = state.card_of(iid)
    if "Gargoyle" in demona.subtypes:
        return any(state.card_of(target).name == "Angela" for target in E._permanents_in_play(state, owner))
    return False


def register() -> None:
    T = E.TRIGGERED_ABILITY_HANDLERS
    A = E.ACTION_EFFECT_HANDLERS
    ACT = E.ACTIVATED_ABILITY_HANDLERS
    RB = E.STATIC_READY_BLOCKERS
    LM = E.STATIC_LORE_MODIFIERS

    # --- Deck 1 ---
    A["Can't Hold It Back Anymore"] = _cant_hold_it_back_anymore
    T[("Cheshire Cat - Inexplicable", "on_boost")] = _cheshire_cat_on_boost
    _dale_spike_suit_register()
    T[("Demona - Scourge of the Wyvern Clan", "on_play")] = _demona_on_play
    RB["Demona - Scourge of the Wyvern Clan"] = _demona_stone_by_day
    ACT["BREAKING RECORDS"] = _dumbo_breaking_records
    ACT["MAKING HISTORY"] = _dumbo_breaking_records
    E.STATIC_GRANTED_ACTIVATED_ABILITIES["Dumbo - Ninth Wonder of the Universe"] = _dumbo_making_history_grant
    T[("Gaston - Superior Archer", "on_play")] = _gaston_watch_this
    _grandmother_willow_register()
    T[("Hades - Looking for a Deal", "on_play")] = _hades_what_dya_say
    ACT["LOOSE CHANGE"] = _hamm_loose_change
    T[("Isis Vanderchill - Ice Queen of St. Canard", "on_play")] = _isis_chill_out
    ACT["THE BOOK KNOWS EVERYTHING"] = _junior_woodchuck_guidebook
    ACT["BIRTHDAY LIGHTS"] = _lantern_birthday_lights
    T[("Mrs. Incredible - Super Stretchy", "on_start_of_turn")] = _mrs_incredible_flexible_thinking
    LM["Mrs. Incredible - Super Stretchy"] = _mrs_incredible_lore_modifier
    A["Ohana Means Family"] = _ohana_means_family
    A["Second Star to the Right"] = _second_star_to_the_right
    T[("Sulley - The New Boss", "on_play")] = _sulley_rehire
    T[("Stitch - Carefree Surfer", "on_play")] = _stitch_ohana
    A["World's Greatest Criminal Mind"] = _worlds_greatest_criminal_mind

    # --- Deck 4 ---
    T[("Aladdin - Doing His Part", "on_play")] = _aladdin_doing_his_part
    E.ACTIVATED_ABILITY_LEGALITY["GOOD AIM"] = _angel_good_aim_legal
    ACT["GOOD AIM"] = _angel_good_aim
    T[("Angela - Night Warrior", "on_play")] = _angel_night_warrior
    E.STATIC_CARD_RESOLVERS.add("Angela - Night Warrior")
    T[("Rapunzel - Tower Defender", "on_play")] = _rapunzel_tower_defender
    ACT["WHAT DID YOU NEED?"] = _launchpad_what_did_you_need

    # Mickey Mouse's Secret Path is an ally-broadcast trigger.
    T[("Mickey Mouse - Expedition Leader", "on_ally_quest")] = _mickey_expedition_leader_secret_path
    ACT["GOOD DOG"] = _pluto_good_dog
    T[("Pocahontas - Guiding the Tribe", "on_play")] = _pocahontas_stay_close
    T[("Pocahontas - Peacekeeper", "on_play")] = _pocahontas_peacekeeper_calming_words

    # --- Deck 2 ---
    T[("Aurora - Holding Court", "on_quest")] = _aurora_royal_welcome
    T[("Bobby Zimuruski - Spray Cheese Kid", "on_play")] = _bobby_so_cheesy
    T[("Elinor - Renowned Diplomat", "on_end_of_turn")] = _elinor_coordinated_efforts
    T[("Mike Wazowski - Heroic Climber", "on_play")] = _mike_find_a_friend
    T[("Mike Wazowski - Heroic Climber", "on_quest")] = _mike_find_a_friend
    T[("Chernabog - Unnatural Force", "on_play")] = _chernabog_unatural_force
    T[("Meilin Lee - Losing Control", "on_play")] = _meilin_losing_control
    T[("Milo Thatch - Getting His Hands Dirty", "on_play")] = _milo_getting_his_hands_dirty
    T[("Milo Thatch - Getting His Hands Dirty", "on_end_of_turn")] = _milo_discard_recovery
    T[("Mowgli - Man Cub", "on_play")] = _mowgli_have_a_better_look
    T[("Nani - Stage Manager", "on_play")] = _nani_thats_your_cue
    T[("The Queen - Devious Disguise", "on_play")] = _queen_devious_disguise_evil_scheme
    LM["The Queen - Devious Disguise"] = _queen_devious_disguise_jealous_heart
    T[("Mushu - Stealthy Dragon", "on_quest")] = _mushu_tip_the_scales
    T[("Ursula - Deceiver", "on_play")] = _ursula_deceiver_never_miss_it
