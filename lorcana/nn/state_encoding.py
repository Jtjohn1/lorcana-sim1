"""GameState -> token features, for the Transformer value network.

Scope, stated explicitly (see transformer.py's docstring for why there's
no positional encoding to compensate for what's left out):

- Tokens are emitted for every card in HAND and PLAY, for both players.
  Both are legitimately visible under this project's default GameConfig
  (open_hands=True), which is the actual game-rules-respecting source of
  truth here, not a shortcut — see lorcana/state.py.
- Inkwell and discard are summarized as scalar counts on the global
  token, not tokenized card-by-card: their face-up identities matter far
  less than their counts for evaluation purposes, and keeping the token
  count bounded matters for a single-core CPU training budget.
- Deck contents are NOT tokenized at all, even though they're visible
  under open_decks=True — a 50-card deck per player would dwarf the
  board/hand token count for comparatively little evaluation value.
  This is a deliberate, documented scope cut, not a rules violation:
  the ENGINE still uses full deck knowledge correctly wherever it
  matters (legality, effects); the network's input is just narrower
  than what a human player with the same open-information config could
  look at.

INPUT_DIM is fixed; every token (card or the one global summary token)
is a vector of this length, with unused slots zeroed per the layout in
`_card_token` / `_global_token`.
"""
from __future__ import annotations

import numpy as np

from ..state import GameState

COLORS = ("Amber", "Amethyst", "Emerald", "Ruby", "Sapphire", "Steel")
TYPES = ("Character", "Action", "Item", "Location")

# Slot layout (see module docstring for the global-only slots 20-27):
#  0: cost/10            4-9:  color one-hot (6)
#  1: strength/10        10-13: type one-hot (4)
#  2: willpower/10       14: zone (hand=0, play=1)
#  3: lore value/5       15: owner (mine=1, opponent=-1, n/a=0)
#                         16: exerted flag
#                         17: dry flag
#                         18: damage/10
#                         19: is_global_token flag
# 20-28: global-only (see _global_token)
INPUT_DIM = 29


def _card_token(state: GameState, iid: int, me: int, zone_flag: float) -> np.ndarray:
    card = state.card_of(iid)
    inst = state.instances[iid]
    v = np.zeros(INPUT_DIM, dtype=np.float64)
    v[0] = (card.cost or 0) / 10.0
    v[1] = (card.strength or 0) / 10.0
    v[2] = (card.willpower or 0) / 10.0
    v[3] = (card.lore or 0) / 5.0
    if card.color in COLORS:
        v[4 + COLORS.index(card.color)] = 1.0
    if card.type in TYPES:
        v[10 + TYPES.index(card.type)] = 1.0
    v[14] = zone_flag
    v[15] = 1.0 if inst.owner == me else -1.0
    v[16] = 1.0 if inst.exerted else 0.0
    v[17] = 1.0 if (zone_flag == 1.0 and state.is_dry(iid)) else 0.0
    v[18] = inst.damage / 10.0
    return v


def _global_token(state: GameState, me: int) -> np.ndarray:
    opp = state.opponent_idx(me)
    v = np.zeros(INPUT_DIM, dtype=np.float64)
    v[19] = 1.0  # is_global_token
    v[20] = state.players[me].lore / 20.0
    v[21] = state.players[opp].lore / 20.0
    v[22] = state.turn_number / 30.0
    v[23] = sum(1 for i in state.players[me].inkwell if not state.instances[i].exerted) / 10.0
    v[24] = len(state.players[me].inkwell) / 10.0
    v[25] = len(state.players[opp].inkwell) / 10.0
    v[26] = len(state.players[me].hand) / 10.0
    v[27] = len(state.players[opp].hand) / 10.0
    # Whose turn it is, from `me`'s perspective. Without this, a state right
    # after "end turn" (now the opponent's turn) and a state mid-way through
    # continuing to act on my own turn look almost identical except for
    # secondary effects (opponent's draw, ink readying) — nothing tells the
    # network these are fundamentally different situations. Added after
    # finding (via direct trace inspection, see project history) that
    # NeuralBot would pick "end turn" over playing/questing almost every
    # time, apparently unable to weigh "I spent a resource but it's still
    # my turn" against "I passed" correctly without this signal.
    v[28] = 1.0 if state.active_idx == me else -1.0
    return v


def encode_state(state: GameState, me: int) -> np.ndarray:
    """Returns a (num_tokens, INPUT_DIM) array: one row per card in HAND
    or PLAY (either player), plus one global summary token."""
    tokens = [_global_token(state, me)]
    for pidx in (me, state.opponent_idx(me)):
        for iid in state.players[pidx].hand:
            tokens.append(_card_token(state, iid, me, zone_flag=0.0))
        for iid in state.players[pidx].play:
            if state.instances[iid].stacked_on is not None:
                continue  # buried under a Shift stack; not meaningfully "in play"
            tokens.append(_card_token(state, iid, me, zone_flag=1.0))
    return np.stack(tokens, axis=0)
