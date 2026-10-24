"""Card definitions (the static, printed data — not in-game state)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "cards.json"


@dataclass(frozen=True)
class Ability:
    type: str  # "keyword" | "triggered" | "activated" | "static"
    name: str | None
    keyword: str | None
    keyword_value: str | None
    keyword_value_number: int | None
    costs: tuple[str, ...]
    effect: str | None
    full_text: str

    @staticmethod
    def from_dict(d: dict) -> "Ability":
        return Ability(
            type=d.get("type"),
            name=d.get("name"),
            keyword=d.get("keyword"),
            keyword_value=d.get("keywordValue"),
            keyword_value_number=d.get("keywordValueNumber"),
            costs=tuple(d.get("costs") or ()),
            effect=d.get("effect"),
            full_text=d.get("fullText", ""),
        )


@dataclass(frozen=True)
class Card:
    """A printed card definition. Immutable — shared by every copy in play."""

    id: int
    set_number: int
    set_name: str
    number: int | None
    name: str
    version: str | None
    full_name: str
    type: str  # "Character" | "Action" | "Item" | "Location"
    color: str | None
    cost: int
    inkwell: bool
    strength: int | None
    willpower: int | None
    lore: int | None
    move_cost: int | None
    subtypes: tuple[str, ...]
    keyword_abilities: tuple[str, ...]
    abilities: tuple[Ability, ...]
    full_text: str
    rarity: str | None
    story: str | None

    @staticmethod
    def from_dict(d: dict) -> "Card":
        return Card(
            id=d["id"],
            set_number=d["setNumber"],
            set_name=d["setName"],
            number=d.get("number"),
            name=d["name"],
            version=d.get("version"),
            full_name=d.get("fullName", d["name"]),
            type=d["type"],
            color=d.get("color"),
            cost=d.get("cost", 0),
            inkwell=d.get("inkwell", False),
            strength=d.get("strength"),
            willpower=d.get("willpower"),
            lore=d.get("lore"),
            move_cost=d.get("moveCost"),
            subtypes=tuple(d.get("subtypes") or ()),
            keyword_abilities=tuple(d.get("keywordAbilities") or ()),
            abilities=tuple(Ability.from_dict(a) for a in d.get("abilities", [])),
            full_text=d.get("fullText", ""),
            rarity=d.get("rarity"),
            story=d.get("story"),
        )

    # --- convenience keyword lookups -------------------------------------
    def has_keyword(self, keyword: str) -> bool:
        """Matches "Shift" against "Shift", "Combo Shift", "Duo Shift", etc."""
        return any(k == keyword or k.endswith(f" {keyword}") for k in self.keyword_abilities)

    def keyword_value(self, keyword: str) -> int | None:
        for a in self.abilities:
            if a.type == "keyword" and a.keyword and (
                a.keyword == keyword or a.keyword.endswith(f" {keyword}")
            ):
                return a.keyword_value_number
        return None

    def is_shift(self) -> bool:
        return self.has_keyword("Shift")

    def shift_targets(self) -> tuple[str, ...]:
        """Character name(s) this card can Shift onto, best-effort from ability text.

        LorcanaJSON doesn't give this as structured data, so we fall back to the
        card's own base name (the common case: Name - Version shifts onto other
        cards named Name). Named Shifts (e.g. onto a specific different character)
        are flagged as unsupported by the engine for now.
        """
        return (self.name,)


class CardDatabase:
    """Loads and indexes the compiled card database (data/cards.json)."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        self.sets: list[dict] = raw["sets"]
        self.by_id: dict[int, Card] = {}
        self.by_name: dict[str, list[Card]] = {}
        for cd in raw["cards"]:
            card = Card.from_dict(cd)
            self.by_id[card.id] = card
            self.by_name.setdefault(card.name, []).append(card)

    def get(self, card_id: int) -> Card:
        return self.by_id[card_id]

    def find_by_full_name(self, full_name: str) -> Card | None:
        for card in self.by_id.values():
            if card.full_name == full_name:
                return card
        return None

    def all_cards(self) -> list[Card]:
        return list(self.by_id.values())

    def __len__(self) -> int:
        return len(self.by_id)
