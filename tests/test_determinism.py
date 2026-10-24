import random

from lorcana import engine
from tests.helpers import make_state


def play_out(seed, max_actions=400):
    s = make_state(seed=seed)
    pick = random.Random(seed)
    for _ in range(max_actions):
        acts = engine.legal_actions(s)
        if not acts:
            break
        engine.apply_action(s, pick.choice(acts))
    return s


def test_same_seed_same_log():
    a, b = play_out(7), play_out(7)
    assert a.log == b.log
    assert (a.winner, a.turn_number) == (b.winner, b.turn_number)


def test_clone_is_independent():
    s = make_state()
    c = s.clone()
    engine.apply_action(c, {"kind": "end_turn"})
    assert s.turn_number == 1 and c.turn_number == 2
    assert s.active_idx == 0
    c.players[0].lore = 5
    assert s.players[0].lore == 0
