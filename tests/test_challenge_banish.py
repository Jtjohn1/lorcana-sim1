from lorcana import engine
from tests.helpers import make_card, make_state, put, find, kw_ability

ATK = make_card(20, "Atk", strength=3, willpower=4)
DEF = make_card(21, "Def", strength=1, willpower=2)
EVASIVE = make_card(22, "Evas", keywords=("Evasive",))
GUARD = make_card(23, "Guard", keywords=("Bodyguard",), willpower=5)
RESISTER = make_card(24, "Resist", willpower=9, keywords=("Resist",), abilities=(kw_ability("Resist", 2),))
RECKLESS = make_card(25, "Rash", strength=2, willpower=3, keywords=("Reckless",))


def challenge(s, atk, dfn):
    acts = find(s, "challenge", attacker_iid=atk, defender_iid=dfn)
    assert acts, "challenge not offered"
    engine.apply_action(s, acts[0])


def test_only_exerted_defenders_can_be_challenged():
    s = make_state(ATK, DEF)
    a = put(s, 0, 20, "play")
    d = put(s, 1, 21, "play", exerted=False)
    assert find(s, "challenge", attacker_iid=a) == []
    s.instances[d].exerted = True
    assert find(s, "challenge", attacker_iid=a, defender_iid=d)


def test_damage_is_simultaneous_and_defender_banished():
    s = make_state(ATK, DEF)
    a = put(s, 0, 20, "play")
    d = put(s, 1, 21, "play", exerted=True)
    challenge(s, a, d)
    assert s.instances[a].exerted is True
    assert s.instances[a].damage == 1                    # took defender's strength
    assert s.instances[d].zone == "discard"              # 3 dmg >= 2 willpower
    assert d in s.players[1].discard and d not in s.players[1].play
    assert s.instances[a].zone == "play"


def test_banish_at_exactly_willpower():
    s = make_state(ATK)
    a = put(s, 0, 20, "play")
    d = put(s, 1, 20, "play", exerted=True)              # willpower 4, takes 3
    challenge(s, a, d)
    assert s.instances[d].zone == "play" and s.instances[d].damage == 3
    s.instances[d].damage = 4
    engine._game_state_check(s)
    assert s.instances[d].zone == "discard"


def test_both_banished_when_trade():
    s = make_state(ATK)
    a = put(s, 0, 20, "play")
    d = put(s, 1, 20, "play", exerted=True)
    s.instances[a].damage = 3
    s.instances[d].damage = 1
    challenge(s, a, d)                                   # each deals 3: both reach >=4
    assert s.instances[a].zone == "discard" and s.instances[d].zone == "discard"


def test_summoning_sick_cannot_challenge():
    s = make_state(ATK, DEF)
    a = put(s, 0, 20, "play", dry=False)
    put(s, 1, 21, "play", exerted=True)
    assert find(s, "challenge", attacker_iid=a) == []


def test_evasive_defender_protected_from_plain_attacker():
    s = make_state(ATK, EVASIVE)
    a = put(s, 0, 20, "play")
    e = put(s, 1, 22, "play", exerted=True)
    assert find(s, "challenge", attacker_iid=a, defender_iid=e) == []


def test_bodyguard_must_be_challenged_first():
    s = make_state(ATK, DEF, GUARD)
    a = put(s, 0, 20, "play")
    plain = put(s, 1, 21, "play", exerted=True)
    guard = put(s, 1, 23, "play", exerted=True)
    assert find(s, "challenge", attacker_iid=a, defender_iid=plain) == []
    assert find(s, "challenge", attacker_iid=a, defender_iid=guard)


def test_resist_reduces_damage():
    s = make_state(ATK, RESISTER)
    a = put(s, 0, 20, "play")
    d = put(s, 1, 24, "play", exerted=True)
    challenge(s, a, d)
    assert s.instances[d].damage == 1                    # 3 - Resist 2


def test_reckless_blocks_end_turn_while_it_can_challenge():
    s = make_state(RECKLESS, DEF)
    put(s, 0, 25, "play")
    put(s, 1, 21, "play", exerted=True)
    assert find(s, "end_turn") == []
