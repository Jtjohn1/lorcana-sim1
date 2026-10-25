"""Evolve a legal deck by selecting and mutating candidate lists.

This is a bounded runner, not an overnight job. Increase generations,
population, and games only after the engine and bot gates are met.
"""
from __future__ import annotations

import argparse
import random
from collections import Counter
from pathlib import Path

from bots.heuristic_bot import HeuristicBot
from bots.random_bot import RandomBot
from bots.search_bot import SearchBot
from lorcana.cards import CardDatabase
from lorcana.deck_mutator import (
    build_card_pool, build_supported_card_pool, card_colors, deck_colors,
    mutate_deck, validate_deck,
)
from lorcana.decklist import load_decklist
from lorcana.coverage import deck_effect_gaps
from self_play import run_one_game

BOT_FACTORIES = {
    "random": RandomBot,
    "heuristic": HeuristicBot,
    "search": SearchBot,
}


def _crossover(rng, db, first, second, pool):
    """Mix two same-color parent decks while enforcing copy/color limits."""
    allowed_colors = deck_colors(db, first)
    target_size = len(first)
    slots = list(first) + [
        cid for cid in second if card_colors(db.get(cid)) <= allowed_colors
    ]
    rng.shuffle(slots)
    child = []
    counts: Counter[str] = Counter()
    for cid in slots:
        card = db.get(cid)
        if card_colors(card) - allowed_colors:
            continue
        if counts[card.full_name] >= 4:
            continue
        child.append(cid)
        counts[card.full_name] += 1
        if len(child) == target_size:
            break

    if len(child) < target_size:
        fill = [
            card for card in pool
            if card_colors(card) <= allowed_colors and counts[card.full_name] < 4
        ]
        while len(child) < target_size and fill:
            card = rng.choice(fill)
            child.append(card.id)
            counts[card.full_name] += 1
            fill = [candidate for candidate in fill if counts[candidate.full_name] < 4]
    if len(child) < target_size:
        return list(first)
    validate_deck(db, child)
    return child


def _require_resolved_effects(db, deck, label):
    gaps = deck_effect_gaps(db, deck)
    if gaps:
        examples = ", ".join(list(gaps)[:6])
        raise ValueError(
            f"{label} has unresolved card text in {len(gaps)} unique cards: {examples}. "
            "Run check_card_coverage.py and add resolvers before deck evolution."
        )

def _evaluate(
    db, deck, opponent, bot_factory, seed, games, generation, candidate_index,
):
    deck = sorted(deck)
    opponent = sorted(opponent)
    points = 0.0
    wins = 0
    losses = 0
    draws = 0

    for game_index in range(games):
        candidate_first = game_index % 2 == 0
        pair_seed = seed + game_index // 2
        game_rng = random.Random(pair_seed)
        bot0 = bot_factory(random.Random(pair_seed ^ 0xA5A5A5A5))
        bot1 = bot_factory(random.Random(pair_seed ^ 0x5A5A5A5A))
        deck0, deck1 = (deck, opponent) if candidate_first else (opponent, deck)
        winner, turns = run_one_game(
            db, game_rng, bot0, bot1,
            deck_a_card_ids=deck0,
            deck_b_card_ids=deck1,
        )
        candidate_won = (winner == 0) if candidate_first else (winner == 1)
        if winner is None:
            draws += 1
            points += 0.5
        elif candidate_won:
            wins += 1
            points += 1.0
        else:
            losses += 1
        print(
            f"  candidate={candidate_index} game={game_index + 1}/{games} "
            f"winner={winner} candidate_won={candidate_won} turns={turns}"
        )

    return {
        "score": points / games,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "deck": list(deck),
        "generation": generation,
    }


def _write_deck(db, deck, path: Path) -> None:
    counts: Counter[str] = Counter(db.get(cid).full_name for cid in deck)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"{count} {name}" for name, count in sorted(counts.items())) + "\n",
        encoding="utf-8",
    )


def evolve(args) -> list[int]:
    db = CardDatabase()
    seed_deck = load_decklist(db, args.seed_deck)
    opponent = load_decklist(db, args.opponent_deck or args.seed_deck)
    validate_deck(db, seed_deck)
    validate_deck(db, opponent)
    _require_resolved_effects(db, seed_deck, "Seed deck")
    _require_resolved_effects(db, opponent, "Opponent deck")
    if args.pool.strip().lower() == "all-supported":
        pool = build_supported_card_pool(db)
    else:
        pool_paths = [part.strip() for part in args.pool.split(",") if part.strip()]
        pool = build_card_pool(db, pool_paths)
    rng = random.Random(args.seed)
    bot_factory = BOT_FACTORIES[args.bot]

    population = [list(seed_deck)]
    while len(population) < args.population:
        parent = rng.choice(population)
        child = mutate_deck(
            rng, db, parent, pool,
            num_mutations=rng.randint(1, args.max_mutations),
        )
        population.append(child)

    cache: dict[tuple[int, ...], dict] = {}
    champion = list(seed_deck)
    champion_score = float("-inf")

    for generation in range(args.generations):
        results = []
        print(f"Generation {generation + 1}/{args.generations}")
        for index, deck in enumerate(population):
            key = tuple(sorted(deck))
            if key not in cache:
                cache[key] = _evaluate(
                    db, deck, opponent, bot_factory, args.seed,
                    args.games_per_deck, generation, index,
                )
            result = cache[key]
            results.append(result)
            print(
                f"  score={result['score']:.3f} "
                f"W-L-D={result['wins']}-{result['losses']}-{result['draws']}"
            )

        results.sort(key=lambda result: result["score"], reverse=True)
        best = results[0]
        if best["score"] > champion_score:
            champion_score = best["score"]
            champion = list(best["deck"])
        print(f"Best generation score: {best['score']:.3f}")

        if generation + 1 == args.generations:
            break

        elite_count = min(args.elite, len(results))
        parents = [result["deck"] for result in results[:elite_count]]
        next_population = [list(parent) for parent in parents]
        while len(next_population) < args.population:
            first = rng.choice(parents)
            same_colors = [
                parent for parent in parents
                if deck_colors(db, parent) == deck_colors(db, first)
            ]
            second = rng.choice(same_colors)
            child = _crossover(rng, db, first, second, pool)
            child = mutate_deck(
                rng, db, child, pool,
                num_mutations=rng.randint(1, args.max_mutations),
            )
            next_population.append(child)
        population = next_population

    validate_deck(db, champion)
    _write_deck(db, champion, Path(args.output))
    print(f"Saved best deck (score={champion_score:.3f}) to {args.output}")
    return champion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-deck", default="data/decklists/deck1.txt")
    parser.add_argument("--opponent-deck", default=None)
    parser.add_argument(
        "--pool",
        default="all-supported",
        help=("Use every card with a known resolver, or provide a comma-separated "
              "list of decklists to restrict the mutation pool."),
    )
    parser.add_argument("--generations", type=int, default=1)
    parser.add_argument("--population", type=int, default=4)
    parser.add_argument("--elite", type=int, default=1)
    parser.add_argument("--games-per-deck", type=int, default=2)
    parser.add_argument("--max-mutations", type=int, default=4)
    parser.add_argument("--bot", choices=BOT_FACTORIES, default="heuristic")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", default="data/evolved_deck.txt")
    args = parser.parse_args()

    if args.generations < 1 or args.population < 2 or args.games_per_deck < 2:
        parser.error("generations >= 1, population >= 2, and games-per-deck >= 2 are required")
    if args.games_per_deck % 2:
        parser.error("games-per-deck must be even so candidates get both player seats")
    if args.elite < 1 or args.elite >= args.population:
        parser.error("elite must be >= 1 and smaller than population")
    if args.max_mutations < 1:
        parser.error("max-mutations must be >= 1")
    evolve(args)


if __name__ == "__main__":
    main()
