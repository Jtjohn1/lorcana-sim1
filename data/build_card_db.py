"""
Merge raw LorcanaJSON `setdata_*.json` files (data/raw/) into one
normalized card database (data/cards.json).

Each card in the output has a stable set of fields the engine relies
on, regardless of which set it came from or minor schema differences
between LorcanaJSON dumps. Run this again whenever new set files are
dropped into data/raw/.

Usage:
    python3 build_card_db.py
"""
from __future__ import annotations

import json
from pathlib import Path

RAW_DIR = Path(__file__).parent / "raw"
OUT_FILE = Path(__file__).parent / "cards.json"


def normalize_card(raw: dict, set_number: int, set_name: str) -> dict:
    abilities = raw.get("abilities", [])
    return {
        "id": raw["id"],  # globally unique across LorcanaJSON sets
        "setNumber": set_number,
        "setName": set_name,
        "number": raw.get("number"),
        "name": raw["name"],
        "version": raw.get("version"),
        "fullName": raw.get("fullName", raw["name"]),
        "type": raw["type"],  # Character | Action | Item | Location
        "color": raw.get("color"),
        "cost": raw.get("cost", 0),
        "inkwell": raw.get("inkwell", False),
        "strength": raw.get("strength"),
        "willpower": raw.get("willpower"),
        "lore": raw.get("lore"),
        "moveCost": raw.get("moveCost"),  # locations: cost to move a character there
        "subtypes": raw.get("subtypes", []),
        "keywordAbilities": raw.get("keywordAbilities", []),
        "abilities": [normalize_ability(a) for a in abilities],
        "fullText": raw.get("fullText", ""),
        "rarity": raw.get("rarity"),
        "story": raw.get("story"),
    }


def normalize_ability(a: dict) -> dict:
    return {
        "type": a.get("type"),  # keyword | triggered | activated | static
        "name": a.get("name"),
        "keyword": a.get("keyword"),
        "keywordValue": a.get("keywordValue"),
        "keywordValueNumber": a.get("keywordValueNumber"),
        "costs": a.get("costs", []),
        "effect": a.get("effect"),
        "fullText": a.get("fullText", ""),
    }


def main() -> None:
    raw_files = sorted(RAW_DIR.glob("setdata_*.json"))
    if not raw_files:
        raise SystemExit(f"No setdata_*.json files found in {RAW_DIR}")

    all_cards: list[dict] = []
    seen_ids: set[int] = set()
    sets_meta = []

    for path in raw_files:
        with open(path, encoding="utf-8") as f:
            set_data = json.load(f)

        set_number = set_data["number"]
        set_name = set_data["name"]
        sets_meta.append({"number": set_number, "name": set_name, "code": set_data.get("code")})

        for raw_card in set_data["cards"]:
            card = normalize_card(raw_card, set_number, set_name)
            if card["id"] in seen_ids:
                raise ValueError(f"Duplicate card id {card['id']} ({card['fullName']})")
            seen_ids.add(card["id"])
            all_cards.append(card)

    out = {"sets": sets_meta, "cards": all_cards}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"Wrote {len(all_cards)} cards from {len(sets_meta)} sets to {OUT_FILE}")


if __name__ == "__main__":
    main()
