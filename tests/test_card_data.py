"""Card data must include sets 9-14 (set 14 = Hyperia City) and stay in sync.

data/cards.json is built from data/raw/setdata_*.json by data/build_card_db.py.
These tests fail if a raw set file is added without rebuilding, or if set 14 is
dropped from either the raw data or the compiled database.
"""
import json
from pathlib import Path

from lorcana.cards import CardDatabase

DATA = Path(__file__).resolve().parent.parent / "data"
REQUIRED_SETS = (9, 10, 11, 12, 13, 14)


def _raw_card_count(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    return len(raw["cards"])


def test_raw_json_exists_for_every_required_set():
    for n in REQUIRED_SETS:
        assert (DATA / "raw" / f"setdata_{n}.json").is_file(), f"missing setdata_{n}.json"


def test_compiled_db_lists_and_contains_set_14():
    db = CardDatabase()
    assert {s["number"] for s in db.sets} >= set(REQUIRED_SETS)
    in_14 = [c for c in db.all_cards() if c.set_number == 14]
    assert in_14, "no set 14 cards in data/cards.json"


def test_compiled_db_card_count_matches_raw_files():
    db = CardDatabase()
    expected = sum(_raw_card_count(DATA / "raw" / f"setdata_{n}.json") for n in REQUIRED_SETS)
    assert len(db.all_cards()) == expected, "cards.json is stale: rerun python data/build_card_db.py"


def test_card_ids_are_unique():
    ids = [c.id for c in CardDatabase().all_cards()]
    assert len(ids) == len(set(ids))
