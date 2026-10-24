from lorcana import engine
from tests.helpers import make_card, make_state, put, find

QUESTER = make_card(30, "Quester", lore=2)


def test_reaching_20_lore_wins():
    s = make_state(QUESTER)
    s.players[0].lore = 18
    c = put(s, 0, 30, "play")
    engine.apply_action(s, find(s, "quest", iid=c)[0])
    assert s.players[0].lore == 20
    assert s.winner == 0 and s.phase == "game_over"


def test_19_lore_does_not_win():
    s = make_state(QUESTER)
    s.players[0].lore = 17
    c = put(s, 0, 30, "play")
    engine.apply_action(s, find(s, "quest", iid=c)[0])
    assert s.winner is None


def test_over_20_still_wins():
    s = make_state(QUESTER)
    s.players[0].lore = 19
    c = put(s, 0, 30, "play")
    engine.apply_action(s, find(s, "quest", iid=c)[0])
    assert s.winner == 0


def test_apply_action_ignored_after_game_over():
    s = make_state(QUESTER)
    s.winner, s.phase = 1, "game_over"
    turn = s.turn_number
    engine.apply_action(s, {"kind": "end_turn"})
    assert s.turn_number == turn


def test_empty_deck_loses_at_end_of_turn_not_before():
    s = make_state()
    p = s.players[0]
    for iid in list(p.deck):
        s.move_zone(iid, "discard")
    assert s.winner is None                              # empty deck alone isn't a loss
    engine.apply_action(s, {"kind": "end_turn"})
    assert s.winner == 1 and s.phase == "game_over"
