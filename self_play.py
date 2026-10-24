"""Run self-play games between two bots and report results.

Usage:
    python3 self_play.py                      # 10 random-vs-random games
    python3 self_play.py --games 50 --bot heuristic
    python3 self_play.py --games 1 --verbose   # print the full move log for one game
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter

from lorcana.cards import CardDatabase
from lorcana.deck_utils import build_random_deck
from lorcana.decklist import load_decklist
from lorcana.engine import new_game, legal_actions, apply_action
from lorcana.state import GameConfig

from bots.random_bot import RandomBot
from bots.heuristic_bot import HeuristicBot
from bots.search_bot import SearchBot
from bots.neural_bot import NeuralBot, load_value_network, make_neural_evaluate_fn

MAX_TURNS = 200  # safety valve against infinite/looping games while effects are unimplemented

_neural_net = None  # lazy-loaded singleton; loading weights per-bot-instance would be wasteful


def _get_neural_net():
    global _neural_net
    if _neural_net is None:
        _neural_net = load_value_network()
    return _neural_net


def _make_neural_bot(rng):
    return NeuralBot(_get_neural_net(), rng)


def _make_search_neural_bot(rng):
    return SearchBot(rng, evaluate_fn=make_neural_evaluate_fn(_get_neural_net()))


BOT_TYPES = {
    "random": RandomBot,
    "heuristic": HeuristicBot,
    "search": SearchBot,
    "neural": _make_neural_bot,          # standalone, one-ply, learned evaluation only
    "search_neural": _make_search_neural_bot,  # full search, learned evaluation at the leaves
}


def run_one_game(db: CardDatabase, rng: random.Random, bot_a, bot_b, verbose: bool = False,
                  deck_a_path: str | None = None, deck_b_path: str | None = None):
    deck_a = load_decklist(db, deck_a_path) if deck_a_path else build_random_deck(db, rng)
    deck_b = load_decklist(db, deck_b_path) if deck_b_path else build_random_deck(db, rng)
    state = new_game(db, deck_a, deck_b, config=GameConfig(), seed=rng.randrange(1 << 30))

    bots = [bot_a, bot_b]
    state.decision_maker = lambda st, pidx, kind, options: bots[pidx].choose_target(st, kind, options)
    turns_taken = 0
    last_turn_number = state.turn_number

    while state.winner is None:
        if state.turn_number != last_turn_number:
            turns_taken += 1
            last_turn_number = state.turn_number
        if turns_taken > MAX_TURNS:
            break
        actions = legal_actions(state)
        if not actions:
            apply_action(state, {"kind": "end_turn"})
            continue
        action = bots[state.active_idx].choose(state)
        apply_action(state, action)

    if verbose:
        print("\n".join(state.log))
        print(f"\nFinal lore: P0={state.players[0].lore} P1={state.players[1].lore}")

    return state.winner, turns_taken


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--bot", choices=BOT_TYPES.keys(), default="random")
    parser.add_argument("--bot2", choices=BOT_TYPES.keys(), default=None, help="Bot type for player 1 (second player). Same as --bot if omitted.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--deck-a", type=str, default=None, help="Path to a decklist text file (e.g. data/decklists/deck1.txt). Random legal deck if omitted.")
    parser.add_argument("--deck-b", type=str, default=None, help="Same as --deck-a, for the second player.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    db = CardDatabase()
    rng = random.Random(args.seed)
    bot_cls = BOT_TYPES[args.bot]
    bot_cls2 = BOT_TYPES[args.bot2] if args.bot2 else bot_cls

    results = Counter()
    turn_counts = []
    for i in range(args.games):
        game_rng = random.Random(rng.randrange(1 << 30))
        winner, turns = run_one_game(
            db, game_rng, bot_cls(game_rng), bot_cls2(game_rng),
            verbose=args.verbose and i == 0, deck_a_path=args.deck_a, deck_b_path=args.deck_b,
        )
        results[winner] += 1
        turn_counts.append(turns)
        print(f"Game {i + 1}/{args.games}: winner={winner} turns={turns}")

    print("\n--- Summary ---")
    print(f"Bot type: P0={args.bot} vs P1={args.bot2 or args.bot}")
    print(f"P0 wins: {results.get(0, 0)}  P1 wins: {results.get(1, 0)}  no-decision (turn limit): {results.get(None, 0)}")
    print(f"Avg turns per game: {sum(turn_counts) / len(turn_counts):.1f}")


if __name__ == "__main__":
    sys.exit(main())
