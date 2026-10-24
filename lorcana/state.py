"""Mutable game state: zones, card instances, players.

Perfect vs. hidden information is a *config* toggle here, not a hardcoded
rule. `GameConfig.open_hands` and `GameConfig.open_decks` decide what a
`GameState.view_for(player)` reveals. Internally the engine always has
full access to everything (this is what a self-play bot uses); the view
layer exists for anything that later needs a fair/restricted picture of
the game (a human player, or an experiment in imperfect-information
search).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .cards import Card, CardDatabase

Zone = str  # "deck" | "hand" | "play" | "inkwell" | "discard"


@dataclass
class GameConfig:
    open_hands: bool = True   # if True, each player's view includes the opponent's hand
    open_decks: bool = True   # if True, each player's view includes full deck order (both decks)
    starting_hand_size: int = 7
    allow_mulligan: bool = False  # MVP: no mulligan step yet (see README "Known gaps")


@dataclass
class CardInstance:
    """One physical copy of a card, as it exists in this specific game."""

    instance_id: int
    card_id: int
    owner: int  # player index
    zone: Zone
    exerted: bool = False
    damage: int = 0
    entered_play_turn: int | None = None  # turn_number this became "dry" from
    at_location: int | None = None  # instance_id of the location it's at, if any
    stacked_on: int | None = None  # instance_id this is Shifted onto (None = base of stack)
    stacked_under: list[int] = field(default_factory=list)  # instance_ids under this one, bottom->top
    boosted_under: int | None = None  # instance_id of the character this is tucked under (Boost)

    def is_dry(self, current_turn: int, controller_turn_started: bool) -> bool:
        """A character is dry once it's been in play since the start of its
        controller's turn (see Comprehensive Rules 1.7.5 / 5.1.1.12)."""
        if self.entered_play_turn is None:
            return False
        return self.entered_play_turn < current_turn


@dataclass
class PlayerState:
    player_id: int
    name: str
    deck: list[int] = field(default_factory=list)     # instance_ids, index 0 = top
    hand: list[int] = field(default_factory=list)
    play: list[int] = field(default_factory=list)
    inkwell: list[int] = field(default_factory=list)
    discard: list[int] = field(default_factory=list)
    under: list[int] = field(default_factory=list)  # cards put "under" a character (e.g. Boost)
    lore: int = 0
    inked_this_turn: bool = False


class GameState:
    def __init__(self, db: CardDatabase, config: GameConfig, seed: int | None = None):
        self.db = db
        self.config = config
        self.rng = random.Random(seed)
        self.players: list[PlayerState] = []
        self.instances: dict[int, CardInstance] = {}
        self._next_instance_id = 1
        self.active_idx = 0
        self.turn_number = 1
        self.phase = "setup"  # setup | start_of_turn | main | end_of_turn | game_over
        self.winner: int | None = None
        self.log: list[str] = []
        self.pending_singer_alt_cost: dict | None = None  # scratch space used mid-action

        # Temporary "this turn" Strength modifiers (e.g. from Support). Cleared
        # at the end of the active player's turn. Keyed by instance_id.
        self.temp_strength_bonus: dict[int, int] = {}

        # "Until the start of your next turn" Strength modifiers (longer-lived
        # than the above — they survive the opponent's whole turn). Each entry:
        # {"iid":, "amount":, "owner": player_idx whose next turn clears it}.
        self.delayed_strength_mods: list[dict] = []

        # "You pay N less for the next character you play this turn" pool.
        # Stacks additively from multiple sources (Grandmother Willow-style
        # per-turn static effects, Hamm/Lantern/Pluto-style activated
        # abilities); consumed (reset to 0) the next time a character is
        # played for its ink cost. Cleared at end of turn if unused.
        self.next_character_discount: int = 0

        # Global "characters can't challenge" lock (e.g. Pocahontas -
        # Peacekeeper's CALMING WORDS). None = no lock. Otherwise the player
        # index whose *next* turn start clears the lock ("until the start of
        # your next turn").
        self.global_no_challenge_until_owner: int | None = None

        # Whether the active player has challenged with any character yet
        # this turn (needed for effects like "if none of your characters
        # challenged this turn"). Reset at the start of every turn.
        self.active_player_challenged_this_turn: bool = False

        # Once-per-character-per-turn tracking for the Boost keyword.
        self.boost_used_this_turn: set[int] = set()

        # Optional hook for sub-decisions made mid-effect-resolution (e.g. "which
        # character does Support boost?"). Signature: fn(state, player_idx, kind,
        # options) -> one of options. If unset, falls back to a random choice —
        # the game never stalls even with no bot wired up for this. Set this to
        # a bot's `choose_target` method (see bots/) before running a game.
        self.decision_maker = None

    def choose(self, player_idx: int, kind: str, options: list):
        if not options:
            return None
        if self.decision_maker is not None:
            return self.decision_maker(self, player_idx, kind, options)
        return self.rng.choice(options)

    # --- fast cloning for search --------------------------------------------
    def clone(self) -> "GameState":
        """A cheap copy for game-tree search: shares immutable/shared data
        (the card database, config) by reference, and independently copies
        everything the engine actually mutates during play. Using
        `copy.deepcopy` here would also recursively copy the ~1,300-card
        database on every simulated move — far too slow for search. Keep
        this in sync with `__init__` whenever a new mutable field is added
        there."""
        new = GameState.__new__(GameState)
        new.db = self.db
        new.config = self.config
        new.rng = random.Random()
        new.rng.setstate(self.rng.getstate())
        new.players = [
            PlayerState(
                player_id=p.player_id, name=p.name,
                deck=list(p.deck), hand=list(p.hand), play=list(p.play),
                inkwell=list(p.inkwell), discard=list(p.discard), under=list(p.under),
                lore=p.lore, inked_this_turn=p.inked_this_turn,
            )
            for p in self.players
        ]
        new.instances = {}
        for iid, inst in self.instances.items():
            new_inst = CardInstance.__new__(CardInstance)
            new_inst.instance_id = inst.instance_id
            new_inst.card_id = inst.card_id
            new_inst.owner = inst.owner
            new_inst.zone = inst.zone
            new_inst.exerted = inst.exerted
            new_inst.damage = inst.damage
            new_inst.entered_play_turn = inst.entered_play_turn
            new_inst.at_location = inst.at_location
            new_inst.stacked_on = inst.stacked_on
            new_inst.stacked_under = list(inst.stacked_under)
            new_inst.boosted_under = inst.boosted_under
            new.instances[iid] = new_inst
        new._next_instance_id = self._next_instance_id
        new.active_idx = self.active_idx
        new.turn_number = self.turn_number
        new.phase = self.phase
        new.winner = self.winner
        new.log = []  # not needed for search; keeps clones cheap
        new.pending_singer_alt_cost = dict(self.pending_singer_alt_cost) if self.pending_singer_alt_cost else None
        new.temp_strength_bonus = dict(self.temp_strength_bonus)
        new.delayed_strength_mods = list(self.delayed_strength_mods)
        new.next_character_discount = self.next_character_discount
        new.global_no_challenge_until_owner = self.global_no_challenge_until_owner
        new.active_player_challenged_this_turn = self.active_player_challenged_this_turn
        new.boost_used_this_turn = set(self.boost_used_this_turn)
        new.decision_maker = self.decision_maker
        return new

    # --- bookkeeping -------------------------------------------------------
    def new_instance(self, card_id: int, owner: int, zone: Zone) -> CardInstance:
        inst = CardInstance(instance_id=self._next_instance_id, card_id=card_id, owner=owner, zone=zone)
        self.instances[inst.instance_id] = inst
        self._next_instance_id += 1
        return inst

    def card_of(self, instance_id: int) -> Card:
        return self.db.get(self.instances[instance_id].card_id)

    def inst(self, instance_id: int) -> CardInstance:
        return self.instances[instance_id]

    def opponent_idx(self, idx: int) -> int:
        return 1 - idx

    def active_player(self) -> PlayerState:
        return self.players[self.active_idx]

    def player_of(self, instance_id: int) -> PlayerState:
        return self.players[self.instances[instance_id].owner]

    def log_event(self, msg: str) -> None:
        self.log.append(f"[T{self.turn_number} P{self.active_idx}] {msg}")

    # --- zone moves ----------------------------------------------------
    def move_zone(self, instance_id: int, new_zone: Zone) -> None:
        inst = self.instances[instance_id]
        owner = self.players[inst.owner]
        for zone_name in ("deck", "hand", "play", "inkwell", "discard", "under"):
            lst = getattr(owner, zone_name)
            if instance_id in lst:
                lst.remove(instance_id)
        getattr(owner, new_zone).append(instance_id)
        inst.zone = new_zone
        if new_zone != "play":
            inst.exerted = False
            inst.damage = 0
            inst.entered_play_turn = None
            inst.at_location = None
            inst.stacked_on = None
            inst.stacked_under = []
        if new_zone != "under":
            inst.boosted_under = None

    def is_dry(self, instance_id: int) -> bool:
        inst = self.instances[instance_id]
        if inst.entered_play_turn is None:
            return False
        return inst.entered_play_turn < self.turn_number

    def characters_in_play(self, player_idx: int) -> list[int]:
        card_db = self.db
        out = []
        for iid in self.players[player_idx].play:
            inst = self.instances[iid]
            if inst.stacked_on is not None:
                continue  # this instance is buried under a Shift stack; not "in play"
            if card_db.get(inst.card_id).type == "Character":
                out.append(iid)
        return out

    def locations_in_play(self, player_idx: int) -> list[int]:
        return [
            iid for iid in self.players[player_idx].play
            if self.db.get(self.instances[iid].card_id).type == "Location"
        ]

    # --- visibility / views --------------------------------------------
    def view_for(self, player_idx: int) -> dict:
        """A redacted view of the state, respecting GameConfig visibility.

        NOTE: the engine itself (and the bundled bots) doesn't consult this —
        it operates on full state, which is what makes self-play with
        open_hands=open_decks=True a genuinely perfect-information game.
        This method exists for cases that need a restricted picture later
        (a human player, or hidden-information experiments).
        """
        opp_idx = self.opponent_idx(player_idx)
        me, opp = self.players[player_idx], self.players[opp_idx]

        def hand_view(p: PlayerState, visible: bool):
            return [self.card_of(i).full_name for i in p.hand] if visible else len(p.hand)

        def deck_view(p: PlayerState, visible: bool):
            return [self.card_of(i).full_name for i in p.deck] if visible else len(p.deck)

        return {
            "turn_number": self.turn_number,
            "active_player": self.active_idx,
            "you": player_idx,
            "your_lore": me.lore,
            "opp_lore": opp.lore,
            "your_hand": hand_view(me, True),
            "opp_hand": hand_view(opp, self.config.open_hands),
            "your_deck": deck_view(me, self.config.open_decks),
            "opp_deck": deck_view(opp, self.config.open_decks),
            "your_play": [self._describe(i) for i in me.play],
            "opp_play": [self._describe(i) for i in opp.play],
            "your_discard": [self.card_of(i).full_name for i in me.discard],
            "opp_discard": [self.card_of(i).full_name for i in opp.discard],
        }

    def _describe(self, iid: int) -> dict:
        inst = self.instances[iid]
        card = self.card_of(iid)
        return {
            "name": card.full_name,
            "exerted": inst.exerted,
            "damage": inst.damage,
            "dry": self.is_dry(iid),
        }
