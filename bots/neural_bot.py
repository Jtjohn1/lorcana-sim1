"""Wraps the trained Transformer value network (lorcana/nn/) for use either
as a standalone bot or as SearchBot's leaf evaluation function.

Two distinct things are worth testing separately, and this module supports
both:
  1. Does the learned evaluation alone produce sensible play? -> NeuralBot
     (one-ply: simulate each legal action, pick the one the network scores
     highest for the active player — no search beyond that).
  2. Does combining it with the existing search algorithm help? ->
     SearchBot(evaluate_fn=make_neural_evaluate_fn(net)) (full minimax /
     alpha-beta / iterative deepening / move ordering, exactly as before,
     with the network swapped in for the leaf judgment).
"""
from __future__ import annotations

import random

from lorcana.engine import legal_actions, apply_action
from lorcana.nn.autodiff import Tensor
from lorcana.nn.state_encoding import encode_state
from lorcana.nn.train import load_weights
from lorcana.nn.transformer import ValueNetwork
from lorcana.nn.state_encoding import INPUT_DIM

DEFAULT_ARCHITECTURE = dict(input_dim=INPUT_DIM, dim=32, num_heads=4, num_layers=2, ff_hidden_dim=64)


def load_value_network(weights_path: str = "lorcana/nn/weights.pkl", seed: int = 7, **arch_overrides) -> ValueNetwork:
    arch = {**DEFAULT_ARCHITECTURE, **arch_overrides}
    net = ValueNetwork(seed=seed, **arch)
    load_weights(net, weights_path)
    return net


def make_neural_evaluate_fn(net: ValueNetwork):
    def evaluate(state, me: int) -> float:
        tokens = encode_state(state, me)
        return net(Tensor(tokens)).data.item()
    return evaluate


class NeuralBot:
    """Standalone one-ply bot driven purely by the learned evaluation — see
    module docstring for why this is tested separately from SearchBot."""

    def __init__(self, net: ValueNetwork, rng: random.Random | None = None):
        self.evaluate = make_neural_evaluate_fn(net)
        self.rng = rng or random.Random()

    def choose(self, state):
        actions = legal_actions(state)
        if not actions:
            return {"kind": "end_turn"}
        if len(actions) == 1:
            return actions[0]

        me = state.active_idx
        best_val = float("-inf")
        best_actions = []
        for a in actions:
            child = state.clone()
            child.decision_maker = lambda st, pidx, kind, opts: self.choose_target(st, kind, opts)
            apply_action(child, a)
            if child.winner is not None:
                val = 1_000_000.0 if child.winner == me else -1_000_000.0
            else:
                val = self.evaluate(child, me)
            if val > best_val:
                best_val = val
                best_actions = [a]
            elif val == best_val:
                best_actions.append(a)
        return self.rng.choice(best_actions)

    def choose_target(self, state, kind, options):
        # Sub-decisions aren't this bot's focus; simple uniform fallback.
        return self.rng.choice(options)
