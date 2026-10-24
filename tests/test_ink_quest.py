from lorcana import engine
from tests.helpers import make_card, make_state, put, add_ready_ink, find

NOINK = make_card(10, "NoInk", cost=1, inkwell=False)
BIG = make_card(11, "Big", cost=3, lore=2)
RECKLESS = make_card(12, "Rash", keywords=("Reckless",))


def test_ink_moves_card_and_only_once_per_turn():
    s = make_state(NOINK)
    noink = put(s, 0, 10, "hand")
    acts = find(s, "ink")
    assert acts and all(a["hand_iid"] != noink for a in acts)   # non-inkable never offered
    hand_before = len(s.players[0].hand)
    engine.apply_action(s, acts[0])
    p = s.players[0]
    assert len(p.inkwell) == 1 and len(p.hand) == hand_before - 1
    assert s.instances[p.inkwell[0]].exerted is False           # enters ready
    assert find(s, "ink") == []                                 # second ink blocked


def test_play_character_costs_ink_and_is_not_dry():
    s = make_state(BIG)
    add_ready_ink(s, 0, 3)
    big = put(s, 0, 11, "hand")
    engine.apply_action(s, find(s, "play_character", hand_iid=big, pay="ink")[0])
    p = s.players[0]
    assert big in p.play and big not in p.hand
    assert sum(s.instances[i].exerted for i in p.inkwell) == s.card_of(big).cost
    assert find(s, "quest", iid=big) == []                      # just played: not dry


def test_cannot_play_without_enough_ink():
    s = make_state(BIG)
    add_ready_ink(s, 0, 2)
    big = put(s, 0, 11, "hand")
    assert find(s, "play_character", hand_iid=big) == []


def test_quest_gains_lore_and_exerts():
    s = make_state(BIG)
    c = put(s, 0, 11, "play")
    engine.apply_action(s, find(s, "quest", iid=c)[0])
    assert s.players[0].lore == s.card_of(c).lore
    assert s.instances[c].exerted is True
    assert find(s, "quest", iid=c) == []                        # exerted: can't quest again


def test_reckless_cannot_quest():
    s = make_state(RECKLESS)
    c = put(s, 0, 12, "play")
    assert find(s, "quest", iid=c) == []
