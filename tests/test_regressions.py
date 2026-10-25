"""One test per issue in PROJECT_AUDIT.md (sections 5, 7, 9).

Tests marked "documents current behavior" pin a known limitation so that
fixing it is a deliberate, visible change: update the test in the same commit.
"""
import inspect

import pytest

from lorcana import engine
from lorcana.state import CardInstance, GameConfig
from tests.helpers import make_card, make_state, put, add_ready_ink, find, FILLER_ID

SONG = make_card(40, "Song3", type="Action", cost=3, subtypes=("Action", "Song"))
BIG_SINGER = make_card(41, "NoSinger4", cost=4)               # cost 4, no Singer keyword
SMALL = make_card(42, "Small2", cost=2)
BODYGUARD = make_card(43, "Guard", cost=2, keywords=("Bodyguard",))
VANILLA_ACTION = make_card(44, "PlainAction", type="Action", cost=1)
TRIG = make_card(45, "Trig", cost=1, effect_text="When you play this character, draw a card.")


# --- Audit 5.1: singing must not require the Singer keyword ------------------
def test_any_character_with_cost_at_least_song_can_sing():
    s = make_state(SONG, BIG_SINGER)
    song = put(s, 0, 40, "hand")
    singer = put(s, 0, 41, "play")
    acts = find(s, "play_action", hand_iid=song, pay="sing")
    assert [a["singers"] for a in acts] == [[singer]]
    engine.apply_action(s, acts[0])
    assert s.instances[singer].exerted is True
    assert s.instances[song].zone == "discard"


def test_character_cheaper_than_song_cannot_sing():
    s = make_state(SONG, SMALL)
    song = put(s, 0, 40, "hand")
    put(s, 0, 42, "play")
    assert find(s, "play_action", hand_iid=song, pay="sing") == []


def test_summoning_sick_or_exerted_character_cannot_sing():
    s = make_state(SONG, BIG_SINGER)
    song = put(s, 0, 40, "hand")
    put(s, 0, 41, "play", dry=False)
    put(s, 0, 41, "play", exerted=True)
    assert find(s, "play_action", hand_iid=song, pay="sing") == []


# --- Audit 5.2: "enter play exerted" variant must be generated ---------------
def test_bodyguard_enter_exerted_variant_is_offered_and_applied():
    s = make_state(BODYGUARD)
    add_ready_ink(s, 0, 2)
    g = put(s, 0, 43, "hand")
    variants = find(s, "play_character", hand_iid=g, pay="ink")
    assert {bool(a.get("enter_exerted")) for a in variants} == {True, False}
    engine.apply_action(s, find(s, "play_character", hand_iid=g, enter_exerted=True)[0])
    assert s.instances[g].exerted is True


def test_non_bodyguard_has_no_enter_exerted_variant():
    s = make_state(SMALL)
    add_ready_ink(s, 0, 2)
    c = put(s, 0, 42, "hand")
    assert find(s, "play_character", hand_iid=c, enter_exerted=True) == []


# --- Audit 5.3: SearchBot node budget shared across candidates ---------------
# Needs bots/search_bot.py, which was not attached. Test to be added once it is.


# --- Audit 5.4: dead CardInstance.is_dry duplicates GameState.is_dry ---------
# The method now takes the controller's turns_started (not the old two-argument
# form). It is still dead code - nothing in lorcana/, bots/ or self_play.py calls
# it - so this test only pins that it agrees with the live GameState.is_dry.
@pytest.mark.skipif(not hasattr(CardInstance, "is_dry"),
                    reason="dead CardInstance.is_dry already removed (milestone 3)")
@pytest.mark.parametrize("started", [0, 1, 2, 3])
@pytest.mark.parametrize("entered", [None, 0, 1, 2])
def test_dead_is_dry_agrees_with_live_one(entered, started):
    s = make_state()
    iid = put(s, 0, FILLER_ID, "play")
    inst = s.instances[iid]
    inst.entered_play_turn = entered
    s.players[0].turns_started = started
    assert inst.is_dry(started) == s.is_dry(iid)


# --- Audit 7: hidden information ---------------------------------------------
# CHANGED ON PURPOSE (codex-work merge): GameConfig now defaults to HIDDEN
# (open_hands = open_decks = False). The engine still never calls view_for();
# SearchBot reads the config flags directly and samples hidden cards.
def test_default_config_hides_hands_and_decks():
    cfg = GameConfig()
    assert cfg.open_hands is False and cfg.open_decks is False


def test_documents_current_behavior_hidden_info_flags_do_not_change_legal_actions():
    """open_hands/open_decks only change view_for(); the engine never calls it."""
    assert "view_for" not in inspect.getsource(engine)
    a = make_state()
    a.config.open_hands = a.config.open_decks = True
    b = make_state()
    b.config.open_hands = b.config.open_decks = False
    assert engine.legal_actions(a) == engine.legal_actions(b)
    assert isinstance(b.view_for(0)["opp_hand"], int)    # redacted: a count
    assert isinstance(a.view_for(0)["opp_hand"], list)   # open: card list


# --- Audit 9: unscripted cards are silent no-ops -----------------------------
def test_documents_current_behavior_unscripted_action_is_logged_noop():
    assert "PlainAction" not in engine.ACTION_EFFECT_HANDLERS
    s = make_state(VANILLA_ACTION)
    add_ready_ink(s, 0, 1)
    a = put(s, 0, 44, "hand")
    engine.apply_action(s, find(s, "play_action", hand_iid=a, pay="ink")[0])
    assert any("no-op" in line for line in s.log)
    assert s.instances[a].zone == "discard"
    assert sum(s.instances[i].exerted for i in s.players[0].inkwell) == 1   # cost still paid


def test_documents_current_behavior_unscripted_trigger_never_fires_and_logs_nothing():
    assert ("Trig", "on_play") not in engine.TRIGGERED_ABILITY_HANDLERS
    s = make_state(TRIG)
    add_ready_ink(s, 0, 1)
    c = put(s, 0, 45, "hand")
    hand_after_play = len(s.players[0].hand) - 1
    engine.apply_action(s, find(s, "play_character", hand_iid=c, pay="ink")[0])
    assert len(s.players[0].hand) == hand_after_play        # no card drawn
    assert [l for l in s.log if "Trig" in l] == [f"[T1 P0] Played Trig"]  # only the play line


# --- Audit 6/12: deck legality is not validated ------------------------------
# Needs lorcana/decklist.py and data/decklists/deck{1,4,5}.txt (not attached).
