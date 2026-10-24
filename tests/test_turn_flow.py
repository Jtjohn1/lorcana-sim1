from lorcana import engine
from tests.helpers import make_state, put, find


def test_opening_state():
    s = make_state()
    assert all(len(p.hand) == 7 for p in s.players)   # 7 drawn, first turn skips the draw
    assert all(len(p.deck) == 13 for p in s.players)
    assert s.phase == "main" and s.winner is None


def test_end_turn_switches_player_and_draws():
    s = make_state()
    before = len(s.players[1].hand)
    engine.apply_action(s, {"kind": "end_turn"})
    assert s.active_idx == 1
    assert s.turn_number == 2
    assert s.phase == "main"
    assert len(s.players[1].hand) == before + 1


def test_ready_step_only_readies_active_players_cards():
    s = make_state()
    mine = put(s, 0, 1, "play", exerted=True)
    theirs = put(s, 1, 1, "play", exerted=True)
    engine.apply_action(s, {"kind": "end_turn"})      # P1's turn starts
    assert s.instances[theirs].exerted is False       # readied
    assert s.instances[mine].exerted is True          # still exerted until my own turn
    engine.apply_action(s, {"kind": "end_turn"})
    assert s.instances[mine].exerted is False


def test_inked_flag_resets_each_turn():
    s = make_state()
    engine.apply_action(s, find(s, "ink")[0])
    assert s.players[0].inked_this_turn is True
    engine.apply_action(s, {"kind": "end_turn"})
    engine.apply_action(s, {"kind": "end_turn"})
    assert s.players[0].inked_this_turn is False


def test_no_actions_once_game_over():
    s = make_state()
    s.winner = 0
    s.phase = "game_over"
    assert engine.legal_actions(s) == []
