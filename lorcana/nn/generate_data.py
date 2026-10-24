"""Generates (state_tokens, outcome_label) training examples via self-play.

Uses the REAL engine (lorcana.engine.legal_actions/apply_action) and REAL
decks (lorcana.decklist) — every game played here is a fully rules-legal
game between two fully rules-legal 60-card decks, same as any other
self-play run in this project. This script does not relax or shortcut
deckbuilding or gameplay rules to make data generation easier.

Label scheme: for each sampled state, encode from the perspective of
whichever player is about to act, label +1.0 if that player goes on to
win the game, -1.0 if they lose. Games that hit the turn-limit safety
valve (no winner) are dropped entirely rather than guessing a label.
RandomBot and HeuristicBot are used to generate games (fast — SearchBot
is far too slow to generate a useful volume of games on one CPU core;
see README for how this dataset's bot mix affects what the network can
plausibly learn).
"""
from __future__ import annotations

import pickle
import random

from ..cards import CardDatabase
from ..decklist import load_decklist
from ..engine import new_game, legal_actions, apply_action
from ..state import GameConfig
from .state_encoding import encode_state

MAX_TURNS = 200
SAMPLES_PER_GAME = 5


def generate_dataset(
    db: CardDatabase,
    deck_a_path: str,
    deck_b_path: str,
    num_games: int,
    rng: random.Random,
    bot_factory,
):
    """bot_factory() -> an object with .choose(state) and .choose_target(state, kind, options)
    (e.g. lambda: RandomBot(random.Random(...)) or a mix — see train.py)."""
    deck_a = load_decklist(db, deck_a_path)
    deck_b = load_decklist(db, deck_b_path)

    examples: list[tuple] = []  # (tokens: np.ndarray, label: float)
    games_used = 0

    for g in range(num_games):
        state = new_game(db, deck_a, deck_b, config=GameConfig(), seed=rng.randrange(1 << 30))
        bots = [bot_factory(), bot_factory()]
        state.decision_maker = lambda st, pidx, kind, opts: bots[pidx].choose_target(st, kind, opts)

        pending: list[tuple] = []  # (tokens, acting_player) sampled this game, label filled in after
        turns = 0
        last_turn = state.turn_number
        decision_count = 0

        while state.winner is None:
            if state.turn_number != last_turn:
                turns += 1
                last_turn = state.turn_number
            if turns > MAX_TURNS:
                break
            actions = legal_actions(state)
            if not actions:
                apply_action(state, {"kind": "end_turn"})
                continue
            decision_count += 1
            # Sample a handful of decision points per game, spread out rather
            # than clustered, to avoid oversampling near-duplicate early-game
            # states.
            if len(pending) < SAMPLES_PER_GAME and rng.random() < 0.08:
                tokens = encode_state(state, state.active_idx)
                pending.append((tokens, state.active_idx))
            a = bots[state.active_idx].choose(state)
            apply_action(state, a)

        if state.winner is None:
            continue  # turn-limit timeout — drop, no reliable label
        games_used += 1
        for tokens, acting_player in pending:
            label = 1.0 if state.winner == acting_player else -1.0
            examples.append((tokens, label))

    return examples, games_used


def save_dataset(examples, path: str) -> None:
    with open(path, "wb") as f:
        pickle.dump(examples, f)


def load_dataset(path: str):
    with open(path, "rb") as f:
        return pickle.load(f)
