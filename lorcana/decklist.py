"""Parse plain-text decklists ("4 Card Name" per line, blank lines and
lines starting with "//" ignored) into lists of card ids the engine can
use directly with `new_game`.
"""
from __future__ import annotations

from .cards import CardDatabase


def parse_decklist_text(text: str) -> list[tuple[int, str]]:
    out = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        parts = line.split(" ", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        out.append((int(parts[0]), parts[1].strip()))
    return out


def decklist_to_card_ids(db: CardDatabase, text: str) -> list[int]:
    """Returns card ids (with duplicates); raises ValueError naming any card
    that isn't in the loaded card database (e.g. from a set not yet added
    to data/raw/ — see data/build_card_db.py)."""
    ids = []
    missing = []
    for count, name in parse_decklist_text(text):
        card = db.find_by_full_name(name)
        if card is None:
            missing.append(name)
            continue
        ids += [card.id] * count
    if missing:
        raise ValueError(f"Cards not found in the card database: {missing}")
    return ids


def load_decklist(db: CardDatabase, path: str) -> list[int]:
    with open(path, encoding="utf-8") as f:
        return decklist_to_card_ids(db, f.read())
