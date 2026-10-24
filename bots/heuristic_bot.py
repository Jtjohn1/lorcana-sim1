"""A step above RandomBot: cheap heuristics, no search.

Priority order per turn (re-evaluated after every action, since the legal
action list changes as state changes):
  1. Take challenges that banish the opponent's character without losing
     your own (rough strength/willpower comparison, ignoring most keyword
     modifiers for simplicity).
  2. Quest with characters that gain lore.
  3. Play the highest-cost card you can afford (curve out).
  4. Ink the lowest-cost inkable card in hand (keep better cards in hand).
  5. Move / anything else.
  6. End turn.

This is meant as a baseline to self-play against and to sanity-check the
engine — not a strong player. See the project README, roadmap step 5,
for where real search-based decision-making would replace this.
"""
from __future__ import annotations

import random

from lorcana.engine import legal_actions, Action
from lorcana.state import GameState


class HeuristicBot:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    def choose(self, state: GameState) -> Action:
        actions = legal_actions(state)
        if not actions:
            return {"kind": "end_turn"}

        scored = [(self._score(state, a), a) for a in actions]
        best_score = max(s for s, _ in scored)
        best = [a for s, a in scored if s == best_score]
        return self.rng.choice(best)

    def choose_target(self, state: GameState, kind: str, options: list):
        """Generic sub-decision hook (see GameState.choose / .decision_maker).
        For Support's "chosen character," boost whichever option currently
        has the highest Strength (biggest hit gets bigger); `None` (decline)
        is only picked if it's the sole option."""
        if kind == "support_target":
            real = [o for o in options if o is not None]
            if not real:
                return None
            return max(real, key=lambda iid: state.card_of(iid).strength or 0)
        return self.rng.choice(options)

    def _score(self, state: GameState, action: Action) -> float:
        kind = action["kind"]

        if kind == "challenge":
            attacker = state.card_of(action["attacker_iid"])
            defender = state.card_of(action["defender_iid"])
            atk_str = attacker.strength or 0
            def_wp = defender.willpower or 1
            atk_wp = attacker.willpower or 1
            def_str = defender.strength or 0
            banishes_defender = atk_str >= def_wp
            loses_attacker = def_str >= atk_wp
            if banishes_defender and not loses_attacker:
                return 100 + (defender.cost or 0)
            if banishes_defender and loses_attacker:
                return 40 + (defender.cost or 0) - (attacker.cost or 0)
            return -50  # bad trade; still legal, but avoid unless forced (e.g. Reckless)

        if kind == "quest":
            card = state.card_of(action["iid"])
            return 60 + 10 * (card.lore or 0)

        if kind in ("play_character", "play_item", "play_location"):
            card = state.card_of(action["hand_iid"])
            return 20 + (card.cost or 0)

        if kind == "play_action":
            card = state.card_of(action["hand_iid"])
            return 15 + (card.cost or 0)

        if kind == "ink":
            card = state.card_of(action["hand_iid"])
            return 10 - (card.cost or 0) * 0.1  # prefer inking cheap/low-impact cards

        if kind == "move":
            return 5

        if kind == "end_turn":
            return 0

        return 0
