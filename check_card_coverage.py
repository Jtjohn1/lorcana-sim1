"""Report cards whose printed abilities do not have a known engine resolver."""
from __future__ import annotations

import argparse

from lorcana.cards import CardDatabase
from lorcana.coverage import card_effect_gaps
from lorcana.decklist import load_decklist


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deck", action="append", default=[],
        help="Also report a decklist; may be supplied more than once.",
    )
    parser.add_argument(
        "--require-resolved-effects", action="store_true",
        help="Exit with status 1 when any selected card has unsupported text.",
    )
    args = parser.parse_args()

    db = CardDatabase()
    gaps_by_card = {
        card.id: card_effect_gaps(card)
        for card in db.all_cards()
    }
    incomplete = 0
    for set_info in db.sets:
        cards = [card for card in db.all_cards() if card.set_number == set_info["number"]]
        unresolved = [card for card in cards if gaps_by_card[card.id]]
        incomplete += len(unresolved)
        print(
            f"Set {set_info['number']} {set_info['name']}: "
            f"{len(cards) - len(unresolved)}/{len(cards)} cards have a known effect resolver"
        )
        for card in unresolved[:3]:
            print(f"  {card.full_name}: {gaps_by_card[card.id][0]}")

    total_cards = len(db.all_cards())
    print(f"Overall: {total_cards - incomplete}/{total_cards} cards have a known effect resolver")

    selected_incomplete = 0
    for path in args.deck:
        ids = load_decklist(db, path)
        cards = {db.get(cid).full_name: db.get(cid) for cid in ids}
        unresolved = [card for card in cards.values() if gaps_by_card[card.id]]
        selected_incomplete += len(unresolved)
        print(
            f"{path}: {len(cards) - len(unresolved)}/{len(cards)} unique cards "
            f"have a known effect resolver"
        )
        for card in unresolved:
            print(f"  {card.full_name}: {gaps_by_card[card.id][0]}")

    if args.require_resolved_effects and (selected_incomplete if args.deck else incomplete):
        print("Unsupported card text remains; large fitness runs are not ready.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())