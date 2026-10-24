"""The simplest possible bot: pick a uniformly random legal action.

Useful as a correctness check for the engine (a full self-play game
between two of these should never crash and should always terminate)
and as a floor to measure smarter bots against.
"""
from __future__ import annotations

import random

from lorcana.engine import legal_actions, Action
from lorcana.state import GameState


class RandomBot:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    def choose(self, state: GameState) -> Action:
        actions = legal_actions(state)
        if not actions:
            return {"kind": "end_turn"}
        return self.rng.choice(actions)

    def choose_target(self, state: GameState, kind: str, options: list):
        """Generic sub-decision hook (see GameState.choose / .decision_maker),
        used mid-effect-resolution for things like Support's "chosen
        character." Picks uniformly at random among the given options."""
        return self.rng.choice(options)
