from __future__ import annotations

import json
import random
import unittest
from pathlib import Path

import lorcana.engine as engine
from lorcana.cards import CardDatabase
from lorcana.engine import (
    _resolve_trigger_bag, _return_temporary_shifts, _run_end_of_turn, _run_start_of_turn,
    apply_action, deal_damage, legal_actions, new_game,
)
from lorcana.deck_mutator import (
    build_card_pool, build_supported_card_pool, deck_colors, mutate_deck, validate_deck,
)
from lorcana.coverage import _trigger_events, card_effect_gaps
from deck_evolution import _crossover, _require_resolved_effects
from lorcana.decklist import load_decklist
from lorcana.state import GameConfig, GameState, PlayerState
from bots.heuristic_bot import HeuristicBot
from bots.targeting import choose_discard, choose_opponent_discard
from bots.random_bot import RandomBot
from bots.search_bot import SearchBot, describe_action


class RulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase()

    def make_state(self):
        state = GameState(self.db, GameConfig(starting_hand_size=0), seed=1)
        state.players = [PlayerState(0, "P0"), PlayerState(1, "P1")]
        state.active_idx = 0
        state.turn_number = 2
        state.players[0].turns_started = 2
        state.players[1].turns_started = 2
        state.phase = "main"
        return state

    def put(self, state, card, owner, zone):
        inst = state.new_instance(card.id, owner, "deck")
        state.move_zone(inst.instance_id, zone)
        if zone == "play":
            inst.entered_play_turn = 1
        return inst.instance_id

    def test_player_views_hide_both_deck_orders_when_closed(self):
        state = GameState(
            self.db, GameConfig(starting_hand_size=0, open_hands=False, open_decks=False), seed=2
        )
        state.players = [PlayerState(0, "P0"), PlayerState(1, "P1")]
        card = next(c for c in self.db.all_cards() if c.inkwell)
        self.put(state, card, 0, "deck")
        self.put(state, card, 1, "deck")

        view = state.view_for(0)
        self.assertEqual(1, view["your_deck"])
        self.assertEqual(1, view["opp_deck"])
        self.assertEqual(0, view["opp_hand"])

        state.config = GameConfig(starting_hand_size=0, open_hands=True, open_decks=True)
        open_view = state.view_for(0)
        self.assertEqual([card.full_name], open_view["your_deck"])
        self.assertEqual([card.full_name], open_view["opp_deck"])

    def test_searchbot_samples_hidden_card_positions_without_mutating_real_state(self):
        state = self.make_state()
        self.assertFalse(GameConfig().open_hands)
        self.assertFalse(GameConfig().open_decks)
        cards = [card for card in self.db.all_cards() if card.inkwell][:8]
        own_hand_iid = self.put(state, cards[0], 0, "hand")
        own_deck = [self.put(state, card, 0, "deck") for card in cards[1:3]]
        opponent_hand = [self.put(state, card, 1, "hand") for card in cards[3:5]]
        opponent_deck = [self.put(state, card, 1, "deck") for card in cards[5:8]]
        original_own_hand = state.instances[own_hand_iid].card_id
        original_own_deck = sorted(state.instances[iid].card_id for iid in own_deck)
        original_opponent_pool = sorted(
            state.instances[iid].card_id for iid in opponent_hand + opponent_deck
        )
        bot = SearchBot(random.Random(23), information_samples=4)

        samples = [bot._sample_hidden_state(state, 0) for _ in range(20)]
        self.assertTrue(all(sample.instances[own_hand_iid].card_id == original_own_hand for sample in samples))
        self.assertTrue(all(
            sorted(sample.instances[iid].card_id for iid in opponent_hand + opponent_deck)
            == original_opponent_pool
            for sample in samples
        ))
        self.assertTrue(all(
            sorted(sample.instances[iid].card_id for iid in own_deck) == original_own_deck
            for sample in samples
        ))
        self.assertTrue(any(
            [sample.instances[iid].card_id for iid in opponent_hand]
            != [state.instances[iid].card_id for iid in opponent_hand]
            for sample in samples
        ))
        self.assertEqual(original_own_hand, state.instances[own_hand_iid].card_id)

    def test_bots_return_legal_actions_and_search_obeys_node_budget(self):
        state = self.make_state()
        hand_cards = [
            c for c in self.db.all_cards()
            if c.type == "Character" and c.inkwell
        ][:8]
        ink_card = next(c for c in self.db.all_cards() if c.inkwell)
        for card in hand_cards:
            self.put(state, card, 0, "hand")
        for _ in range(4):
            self.put(state, ink_card, 0, "inkwell")

        bots = [
            RandomBot(random.Random(1)),
            HeuristicBot(random.Random(1)),
            SearchBot(random.Random(1), max_depth=8, max_nodes=30, max_total_nodes=40, verbose=True),
        ]
        for bot in bots:
            action = bot.choose(state)
            self.assertIn(action, legal_actions(state))
            if isinstance(bot, SearchBot):
                self.assertLessEqual(bot._total_nodes_visited, bot.max_total_nodes)
                self.assertEqual(describe_action(state, action), bot.trace_log[-1]["chosen"])
                self.assertEqual(4, bot.trace_log[-1]["information_samples"])
            apply_action(state.clone(), action)

    def test_search_bot_can_describe_multi_target_shift(self):
        state = self.make_state()
        duo = self.db.find_by_full_name("Mickey Mouse & Minnie Mouse - Adventuring Duo")
        mickey = self.db.find_by_full_name("Mickey Mouse - Inquisitive Explorer")
        minnie = self.db.find_by_full_name("Minnie Mouse - Curious Adventurer")
        hand_iid = self.put(state, duo, 0, "hand")
        self.put(state, mickey, 0, "play")
        self.put(state, minnie, 0, "play")
        action = next(
            a for a in legal_actions(state)
            if a.get("hand_iid") == hand_iid and a.get("pay") == "shift"
        )
        self.assertIn("Mickey Mouse", describe_action(state, action))
        self.assertIn("Minnie Mouse", describe_action(state, action))

    def test_challenge_banish_trigger_is_not_classified_as_generic_banish(self):
        self.assertEqual(
            ["on_banish_in_challenge"],
            _trigger_events("When this character is banished in a challenge, draw a card."),
        )
        self.assertEqual(
            ["on_banish"],
            _trigger_events("When this character is banished, draw a card."),
        )

    def test_card_effect_coverage_recognizes_generic_and_reports_unknown_text(self):
        elsa = self.db.find_by_full_name("Elsa - Exploring the Unknown")
        cheshire = self.db.find_by_full_name("Cheshire Cat - Inexplicable")
        self.assertEqual([], card_effect_gaps(elsa))
        self.assertEqual([], card_effect_gaps(cheshire))
        dumbo = self.db.find_by_full_name("Dumbo - Ninth Wonder of the Universe")
        self.assertEqual([], card_effect_gaps(dumbo))
        bobby = self.db.find_by_full_name("Bobby Zimuruski - Spray Cheese Kid")
        tod = self.db.find_by_full_name("Tod - Clever Fox")
        daisy = self.db.find_by_full_name("Daisy Duck - Musketeer Spy")
        tinker = self.db.find_by_full_name("Tinker Bell - Giant Fairy")
        gurgi = self.db.find_by_full_name("Gurgi - Apple Lover")
        circle = self.db.find_by_full_name("Circle of Life")
        malicious = self.db.find_by_full_name("Malicious, Mean, and Scary")
        akood = self.db.find_by_full_name("Akood et Emuti")
        under_sea = self.db.find_by_full_name("Under the Sea")
        prince_phillip = self.db.find_by_full_name("Prince Phillip - Vanquisher of Foes")
        meilin = self.db.find_by_full_name("Meilin Lee - Losing Control")
        chernabog = self.db.find_by_full_name("Chernabog - Unnatural Force")
        milo = self.db.find_by_full_name("Milo Thatch - Getting His Hands Dirty")
        self.assertEqual([], card_effect_gaps(bobby))
        self.assertEqual([], card_effect_gaps(tod))
        self.assertEqual([], card_effect_gaps(daisy))
        self.assertEqual([], card_effect_gaps(gurgi))
        self.assertEqual([], card_effect_gaps(circle))
        self.assertEqual([], card_effect_gaps(malicious))
        self.assertEqual([], card_effect_gaps(akood))
        self.assertEqual([], card_effect_gaps(under_sea))
        self.assertEqual([], card_effect_gaps(prince_phillip))
        self.assertEqual([], card_effect_gaps(meilin))
        self.assertEqual([], card_effect_gaps(chernabog))
        self.assertEqual([], card_effect_gaps(milo))
        lenny = self.db.find_by_full_name("Lenny - Toy Binoculars")
        broadway = self.db.find_by_full_name("Broadway - Sturdy and Strong")
        snow_fort = self.db.find_by_full_name("Snow Fort")
        vixey = self.db.find_by_full_name("Vixey - Forest Friend")
        self.assertEqual([], card_effect_gaps(lenny))
        self.assertEqual([], card_effect_gaps(broadway))
        self.assertEqual([], card_effect_gaps(snow_fort))
        self.assertEqual([], card_effect_gaps(vixey))

    def test_chernabog_shuffles_opponent_character_then_offers_free_discard_play(self):
        state = self.make_state()
        chernabog = self.db.find_by_full_name("Chernabog - Unnatural Force")
        chernabog_iid = self.put(state, chernabog, 0, "hand")
        target_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and not card.has_keyword("Ward")
        )
        target_iid = self.put(state, target_card, 1, "play")
        discard_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.id != target_card.id
        )
        discard_iid = self.put(state, discard_card, 1, "discard")
        for _ in range(chernabog.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        bot = HeuristicBot(random.Random(19))
        state.decision_maker = lambda st, pidx, kind, options: bot.choose_target(st, kind, options)

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == chernabog_iid
        )
        apply_action(state, play)
        self.assertEqual("deck", state.instances[target_iid].zone)
        self.assertIn(discard_iid, state.players[1].play)

    def test_milo_end_trigger_counts_cards_that_entered_discard_this_turn(self):
        state = self.make_state()
        milo = self.db.find_by_full_name("Milo Thatch - Getting His Hands Dirty")
        self.put(state, milo, 0, "play")
        discard_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(2):
            iid = self.put(state, discard_card, 0, "hand")
            state.move_zone(iid, "discard")
        top_card = next(card for card in self.db.all_cards() if card.inkwell)
        drawn_iid = self.put(state, top_card, 0, "deck")
        extra_card = next(card for card in self.db.all_cards() if card.id != top_card.id)
        self.put(state, extra_card, 0, "deck")
        self.assertEqual(2, state.cards_discarded_this_turn[0])

        _run_end_of_turn(state)
        self.assertIn(drawn_iid, state.players[0].hand)

    def test_milo_discards_to_return_a_chosen_character_to_its_owners_hand(self):
        state = self.make_state()
        milo = self.db.find_by_full_name("Milo Thatch - Getting His Hands Dirty")
        milo_iid = self.put(state, milo, 0, "hand")
        discard_card = next(card for card in self.db.all_cards() if card.inkwell)
        discard_iid = self.put(state, discard_card, 0, "hand")
        target_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and not card.has_keyword("Ward")
        )
        target_iid = self.put(state, target_card, 1, "play")
        for _ in range(milo.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        bot = HeuristicBot(random.Random(20))
        state.decision_maker = lambda st, pidx, kind, options: bot.choose_target(st, kind, options)

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == milo_iid
        )
        apply_action(state, play)
        self.assertIn(discard_iid, state.players[0].discard)
        self.assertIn(target_iid, state.players[1].hand)
        self.assertNotIn(target_iid, state.players[1].play)

    def test_meilin_looks_at_four_and_takes_red_panda_or_song(self):
        state = self.make_state()
        meilin = self.db.find_by_full_name("Meilin Lee - Losing Control")
        meilin_iid = self.put(state, meilin, 0, "hand")
        red_panda = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and "Red Panda" in card.subtypes
        )
        filler = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and "Red Panda" not in card.subtypes
            and card.type != "Action"
        )
        top_ids = [self.put(state, card, 0, "deck") for card in (red_panda, filler, filler, filler)]
        for _ in range(meilin.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        bot = HeuristicBot(random.Random(11))
        state.decision_maker = lambda st, _pidx, kind, options: bot.choose_target(st, kind, options)

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == meilin_iid
        )
        apply_action(state, play)
        self.assertIn(top_ids[0], state.players[0].hand)
        self.assertEqual(top_ids[1:], state.players[0].deck)

    def test_generic_play_trigger_banishes_all_damaged_opposing_characters(self):
        state = self.make_state()
        prince = self.db.find_by_full_name("Prince Phillip - Vanquisher of Foes")
        prince_iid = self.put(state, prince, 0, "hand")
        damaged_card = next(card for card in self.db.all_cards() if card.type == "Character")
        undamaged_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.id != damaged_card.id
        )
        damaged_iid = self.put(state, damaged_card, 1, "play")
        undamaged_iid = self.put(state, undamaged_card, 1, "play")
        state.instances[damaged_iid].damage = 1
        for _ in range(prince.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == prince_iid
        )
        apply_action(state, play)
        self.assertEqual("discard", state.instances[damaged_iid].zone)
        self.assertEqual("play", state.instances[undamaged_iid].zone)

    def test_generic_song_action_adds_next_character_discount_and_draws(self):
        state = self.make_state()
        song = self.db.find_by_full_name("Akood et Emuti")
        song_iid = self.put(state, song, 0, "hand")
        drawn_card = next(card for card in self.db.all_cards() if card.inkwell)
        drawn_iid = self.put(state, drawn_card, 0, "deck")
        for _ in range(song.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_action" and action.get("hand_iid") == song_iid
            and action.get("pay") == "ink"
        )
        apply_action(state, play)
        self.assertEqual(2, state.next_character_discount)
        self.assertIn(drawn_iid, state.players[0].hand)

    def test_generic_song_bottom_decks_only_characters_with_low_strength(self):
        state = self.make_state()
        song = self.db.find_by_full_name("Under the Sea")
        song_iid = self.put(state, song, 0, "hand")
        weak = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and (card.strength or 0) <= 2
            and not card.has_keyword("Ward")
        )
        strong = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and (card.strength or 0) > 2
            and not card.has_keyword("Ward")
        )
        weak_iid = self.put(state, weak, 1, "play")
        strong_iid = self.put(state, strong, 1, "play")
        for _ in range(song.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_action" and action.get("hand_iid") == song_iid
            and action.get("pay") == "ink"
        )
        apply_action(state, play)
        self.assertIn(weak_iid, state.players[1].deck)
        self.assertNotIn(weak_iid, state.players[1].play)
        self.assertIn(strong_iid, state.players[1].play)

    def test_generic_song_plays_character_from_discard_for_free(self):
        state = self.make_state()
        song = self.db.find_by_full_name("Circle of Life")
        song_iid = self.put(state, song, 0, "hand")
        character = max(
            (card for card in self.db.all_cards() if card.type == "Character"),
            key=lambda card: card.cost + (card.lore or 0),
        )
        character_iid = self.put(state, character, 0, "discard")
        for _ in range(song.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        bot = HeuristicBot(random.Random(8))
        state.decision_maker = lambda st, _pidx, kind, options: bot.choose_target(st, kind, options)

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_action" and action.get("hand_iid") == song_iid
            and action.get("pay") == "ink"
        )
        apply_action(state, play)
        self.assertIn(character_iid, state.players[0].play)
        self.assertEqual("play", state.instances[character_iid].zone)

    def test_generic_song_puts_damage_counters_without_resist_reduction(self):
        state = self.make_state()
        song = self.db.find_by_full_name("Malicious, Mean, and Scary")
        song_iid = self.put(state, song, 0, "hand")
        resistant = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.keyword_value("Resist") == 1
            and not card.has_keyword("Ward")
        )
        target_iid = self.put(state, resistant, 1, "play")
        for _ in range(song.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_action" and action.get("hand_iid") == song_iid
            and action.get("pay") == "ink"
        )
        apply_action(state, play)
        self.assertEqual(1, state.instances[target_iid].damage)

    def test_underdog_discount_only_applies_on_second_players_first_turn(self):
        angel = self.db.find_by_full_name("Angel - Siren Singer")
        self.assertEqual([], card_effect_gaps(angel))
        state = self.make_state()
        state.players[0].turns_started = 1
        state.first_player_idx = 1
        angel_iid = self.put(state, angel, 0, "hand")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        ink_iid = self.put(state, ink_card, 0, "inkwell")
        discounted = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == angel_iid
        )
        apply_action(state, discounted)
        self.assertTrue(state.instances[ink_iid].exerted)

        first_player_state = self.make_state()
        first_player_state.players[0].turns_started = 1
        first_player_state.first_player_idx = 0
        first_player_angel = self.put(first_player_state, angel, 0, "hand")
        self.put(first_player_state, ink_card, 0, "inkwell")
        self.assertFalse(any(
            action.get("kind") == "play_character"
            and action.get("hand_iid") == first_player_angel
            for action in legal_actions(first_player_state)
        ))

    def test_generic_optional_draw_discard_trigger_resolves_and_bot_discards_weakest(self):
        state = self.make_state()
        bobby = self.db.find_by_full_name("Bobby Zimuruski - Spray Cheese Kid")
        bobby_iid = self.put(state, bobby, 0, "hand")
        candidates = [
            card for card in self.db.all_cards()
            if card.type == "Character" and card.id != bobby.id
        ]
        card_value = lambda card: (
            1.25 * card.cost + (card.strength or 0)
            + 0.5 * (card.willpower or 0) + 2 * (card.lore or 0)
            + 1.5 * len(card.keyword_abilities)
            + 2 * sum(ability.type != "keyword" for ability in card.abilities)
        )
        weak_card = min(candidates, key=card_value)
        strong_card = max(candidates, key=card_value)
        weak_iid = self.put(state, weak_card, 0, "hand")
        strong_iid = self.put(state, strong_card, 0, "deck")
        for _ in range(bobby.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        bot = HeuristicBot(random.Random(2))
        self.assertEqual(
            weak_iid, bot.choose_target(state, "discard_choice", [weak_iid, strong_iid])
        )
        self.assertEqual(
            weak_iid,
            SearchBot(random.Random(3)).choose_target(
                state, "discard_choice", [weak_iid, strong_iid]
            ),
        )
        state.decision_maker = lambda st, pidx, kind, options: bot.choose_target(st, kind, options)

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == bobby_iid
        )
        apply_action(state, play)
        self.assertIn(strong_iid, state.players[0].hand)
        self.assertIn(weak_iid, state.players[0].discard)
        self.assertNotIn(strong_iid, state.players[0].discard)

    def test_lenny_discards_the_opponents_action_chosen_by_its_controller(self):
        state = self.make_state()
        lenny = self.db.find_by_full_name("Lenny - Toy Binoculars")
        lenny_iid = self.put(state, lenny, 0, "hand")
        actions = [
            card for card in self.db.all_cards()
            if card.type == "Action" and card.id != lenny.id
        ]
        low_action = min(actions, key=lambda card: card.cost + len(card.abilities))
        high_action = max(actions, key=lambda card: card.cost + len(card.abilities))
        low_iid = self.put(state, low_action, 1, "hand")
        high_iid = self.put(state, high_action, 1, "hand")
        for _ in range(lenny.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        bot = HeuristicBot(random.Random(5))
        expected_discard = choose_opponent_discard(state, [low_iid, high_iid])
        state.decision_maker = lambda st, _pidx, kind, options: bot.choose_target(st, kind, options)

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == lenny_iid
        )
        apply_action(state, play)
        self.assertIn(expected_discard, state.players[1].discard)
        self.assertEqual(1, len(state.players[1].hand))

    def test_once_per_turn_action_play_trigger_only_readies_once(self):
        state = self.make_state()
        lenny = self.db.find_by_full_name("Lenny - Toy Binoculars")
        lenny_iid = self.put(state, lenny, 0, "play")
        state.instances[lenny_iid].exerted = True
        state.players[0].turns_started = 2
        action_card = self.db.find_by_full_name("One Jump Ahead")
        actions_in_hand = [self.put(state, action_card, 0, "hand") for _ in range(2)]
        for _ in range(action_card.cost * 2):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        state.decision_maker = lambda _st, _pidx, _kind, options: options[0]

        for action_iid in actions_in_hand:
            play = next(
                action for action in legal_actions(state)
                if action.get("kind") == "play_action" and action.get("hand_iid") == action_iid
                and action.get("pay") == "ink"
            )
            apply_action(state, play)
            if action_iid == actions_in_hand[0]:
                self.assertFalse(state.instances[lenny_iid].exerted)
                state.instances[lenny_iid].exerted = True
        self.assertTrue(state.instances[lenny_iid].exerted)
        self.assertEqual(2, state.once_per_turn_trigger_usage[(lenny_iid, "on_play_action")])

    def test_generic_draw_two_discard_one_trigger_resolves_counts(self):
        state = self.make_state()
        tod = self.db.find_by_full_name("Tod - Clever Fox")
        tod_iid = self.put(state, tod, 0, "hand")
        drawn = [
            self.put(state, card, 0, "deck")
            for card in [card for card in self.db.all_cards() if card.inkwell][:2]
        ]
        for _ in range(tod.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        state.decision_maker = lambda _st, _pidx, _kind, options: options[0]

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == tod_iid
        )
        apply_action(state, play)
        self.assertTrue(all(
            iid in state.players[0].hand or iid in state.players[0].discard
            for iid in drawn
        ))
        self.assertEqual(1, len(state.players[0].hand))
        self.assertEqual(1, len(state.players[0].discard))

    def test_generic_each_opponent_discard_trigger_resolves(self):
        state = self.make_state()
        daisy = self.db.find_by_full_name("Daisy Duck - Musketeer Spy")
        daisy_iid = self.put(state, daisy, 0, "hand")
        opponent_cards = [card for card in self.db.all_cards() if card.inkwell][:2]
        for card in opponent_cards:
            self.put(state, card, 1, "hand")
        for _ in range(daisy.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        state.decision_maker = lambda _st, _pidx, _kind, options: options[0]

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == daisy_iid
        )
        apply_action(state, play)
        self.assertEqual(1, len(state.players[1].hand))
        self.assertEqual(1, len(state.players[1].discard))

    def test_generic_damage_each_opposing_character_applies_resist(self):
        state = self.make_state()
        tinker = self.db.find_by_full_name("Tinker Bell - Giant Fairy")
        tinker_iid = self.put(state, tinker, 0, "hand")
        plain = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.keyword_value("Resist") is None
            and not card.has_keyword("Ward")
        )
        resistant = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.keyword_value("Resist") == 1
            and not card.has_keyword("Ward")
        )
        plain_iid = self.put(state, plain, 1, "play")
        resistant_iid = self.put(state, resistant, 1, "play")
        for _ in range(tinker.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == tinker_iid
        )
        apply_action(state, play)
        self.assertEqual(1, state.instances[plain_iid].damage)
        self.assertEqual(0, state.instances[resistant_iid].damage)

    def test_generic_optional_heal_trigger_chooses_a_damaged_character(self):
        state = self.make_state()
        gurgi = self.db.find_by_full_name("Gurgi - Apple Lover")
        gurgi_iid = self.put(state, gurgi, 0, "hand")
        damaged = next(card for card in self.db.all_cards() if card.type == "Character")
        damaged_iid = self.put(state, damaged, 0, "play")
        state.instances[damaged_iid].damage = 2
        for _ in range(gurgi.cost):
            ink_card = next(card for card in self.db.all_cards() if card.inkwell)
            self.put(state, ink_card, 0, "inkwell")
        bot = HeuristicBot(random.Random(17))
        state.decision_maker = lambda st, _pidx, kind, options: bot.choose_target(st, kind, options)

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == gurgi_iid
        )
        apply_action(state, play)
        self.assertEqual(0, state.instances[damaged_iid].damage)

    def test_deck_evolution_stops_on_cards_with_unresolved_effects(self):
        unsupported = next(card for card in self.db.all_cards() if card_effect_gaps(card))
        deck = [unsupported.id]
        with self.assertRaisesRegex(ValueError, "unresolved card text"):
            _require_resolved_effects(self.db, deck, "Seed deck")

    def test_generic_static_strength_and_opponents_turn_resist(self):
        state = self.make_state()
        snow_fort = self.db.find_by_full_name("Snow Fort")
        self.put(state, snow_fort, 0, "play")
        target_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.keyword_value("Resist") is None
        )
        target_iid = self.put(state, target_card, 0, "play")
        self.assertEqual(
            (target_card.strength or 0) + 1,
            engine._effective_strength(state, target_iid, is_challenger=False),
        )
        state.active_idx = 0
        self.assertEqual(2, engine._damage_after_resist(state, target_iid, 2))
        state.active_idx = 1
        self.assertEqual(1, engine._damage_after_resist(state, target_iid, 2))

    def test_generic_static_resist_from_empty_hand_turns_on_and_off(self):
        state = self.make_state()
        angel = self.db.find_by_full_name("Angel - Experiment 624")
        angel_iid = self.put(state, angel, 0, "play")
        self.assertEqual(1, engine._damage_after_resist(state, angel_iid, 3))
        hand_card = next(card for card in self.db.all_cards() if card.inkwell)
        self.put(state, hand_card, 0, "hand")
        self.assertEqual(3, engine._damage_after_resist(state, angel_iid, 3))

    def test_generic_static_hand_threshold_blocks_ready(self):
        card = self.db.find_by_full_name("Broadway - Sturdy and Strong")
        state = self.make_state()
        broadway_iid = self.put(state, card, 0, "play")
        state.instances[broadway_iid].exerted = True
        hand_card = next(candidate for candidate in self.db.all_cards() if candidate.inkwell)
        for _ in range(3):
            self.put(state, hand_card, 0, "hand")
        _run_start_of_turn(state)
        self.assertTrue(state.instances[broadway_iid].exerted)

    def test_named_character_discount_applies_to_vixey_only_with_tod_in_play(self):
        vixey = self.db.find_by_full_name("Vixey - Forest Friend")
        tod = self.db.find_by_full_name("Tod - Clever Fox")
        state = self.make_state()
        self.put(state, tod, 0, "play")
        vixey_iid = self.put(state, vixey, 0, "hand")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(max(vixey.cost - 1, 0)):
            self.put(state, ink_card, 0, "inkwell")
        self.assertTrue(any(
            action.get("kind") == "play_character" and action.get("hand_iid") == vixey_iid
            for action in legal_actions(state)
        ))

    def test_default_mutation_pool_covers_all_supported_sets(self):
        pool = build_supported_card_pool(self.db)
        self.assertTrue(pool)
        self.assertTrue(all(not card_effect_gaps(card) for card in pool))
        self.assertGreater(len(pool), 100)
        self.assertTrue(any(card.set_number == 14 for card in pool))
        deck = load_decklist(self.db, "data/decklists/deck1.txt")
        changed = mutate_deck(random.Random(29), self.db, deck, pool, num_mutations=20)
        validate_deck(self.db, changed)
        self.assertNotEqual(deck, changed)
        self.assertLessEqual(deck_colors(self.db, changed), deck_colors(self.db, deck))
        self.assertTrue(all(not card_effect_gaps(self.db.get(cid)) for cid in changed))

    def test_deck_mutations_keep_a_legal_two_ink_deck_and_support_dual_ink(self):
        deck = load_decklist(self.db, "data/decklists/deck1.txt")
        pool = build_card_pool(
            self.db,
            ["data/decklists/deck1.txt", "data/decklists/deck2.txt"],
        )
        self.assertTrue(pool)
        self.assertTrue(all(not card_effect_gaps(card) for card in pool))
        colors = deck_colors(self.db, deck)
        self.assertLessEqual(len(colors), 2)
        dual_ink = next(c for c in self.db.all_cards() if c.color and "-" in c.color)
        self.assertTrue(set(dual_ink.color.split("-")) <= deck_colors(self.db, [dual_ink.id]))

        changed = mutate_deck(random.Random(4), self.db, deck, pool, num_mutations=20)
        validate_deck(self.db, changed)
        self.assertEqual(len(deck), len(changed))
        self.assertNotEqual(deck, changed)

    def test_evolution_crossover_keeps_parent_color_and_copy_limits(self):
        first = load_decklist(self.db, "data/decklists/deck1.txt")
        pool = build_card_pool(self.db, ["data/decklists/deck1.txt", "data/decklists/deck2.txt"])
        child = _crossover(random.Random(12), self.db, first, first, pool)

        validate_deck(self.db, child)
        self.assertEqual(len(first), len(child))
        self.assertEqual(deck_colors(self.db, first), deck_colors(self.db, child))

    def test_card_database_contains_sets_9_through_14(self):
        self.assertEqual({9, 10, 11, 12, 13, 14}, {c.set_number for c in self.db.all_cards()})
        self.assertTrue(any(c.set_number == 14 for c in self.db.all_cards()))
        self.assertEqual(1558, len(self.db.all_cards()))

        for set_number in range(9, 15):
            path = Path(f"data/raw/setdata_{set_number}.json")
            self.assertTrue(path.is_file())
            with path.open(encoding="utf-8") as raw_file:
                snapshot = json.load(raw_file)
            self.assertEqual(set_number, snapshot["number"])
            self.assertTrue(snapshot["cards"])


    def test_generic_optional_on_play_draw_uses_bot_choice(self):
        state = self.make_state()
        elsa = self.db.find_by_full_name("Elsa - Exploring the Unknown")
        hand_iid = self.put(state, elsa, 0, "hand")
        drawn = next(card for card in self.db.all_cards() if card.inkwell)
        drawn_iid = self.put(state, drawn, 0, "deck")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(elsa.cost):
            self.put(state, ink_card, 0, "inkwell")

        bot = HeuristicBot(random.Random(3))
        state.decision_maker = lambda st, pidx, kind, options: bot.choose_target(st, kind, options)
        self.assertTrue(SearchBot(random.Random(4)).choose_target(
            state, "generic_optional:0:draw", [True, False]
        ))
        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == hand_iid
            and action.get("pay") == "ink"
        )
        apply_action(state, play)

        self.assertIn(drawn_iid, state.players[0].hand)

    def test_generic_simple_set14_play_and_quest_triggers(self):
        state = self.make_state()
        merlin = self.db.find_by_full_name("Merlin - Profoundly Curious")
        merlin_iid = self.put(state, merlin, 0, "hand")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(merlin.cost):
            self.put(state, ink_card, 0, "inkwell")
        drawn_card = next(card for card in self.db.all_cards() if card.inkwell and card.id != merlin.id)
        drawn_iid = self.put(state, drawn_card, 0, "deck")

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == merlin_iid
        )
        apply_action(state, play)
        self.assertEqual(1, state.players[0].ink_drops)

        state.players[0].turns_started += 1
        quest = next(
            action for action in legal_actions(state)
            if action.get("kind") == "quest" and action.get("iid") == merlin_iid
        )
        apply_action(state, quest)
        self.assertIn(drawn_iid, state.players[0].hand)

    def test_generic_simple_item_play_trigger_draws_a_card(self):
        state = self.make_state()
        item = self.db.find_by_full_name("Ancestral Guitar")
        item_iid = self.put(state, item, 0, "hand")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(item.cost):
            self.put(state, ink_card, 0, "inkwell")
        drawn_card = next(card for card in self.db.all_cards() if card.inkwell and card.id != item.id)
        drawn_iid = self.put(state, drawn_card, 0, "deck")

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_item" and action.get("hand_iid") == item_iid
        )
        apply_action(state, play)
        self.assertIn(drawn_iid, state.players[0].hand)

    def test_generic_simple_activated_ability_gains_lore(self):
        state = self.make_state()
        brooklyn = self.db.find_by_full_name("Brooklyn - Full Throttle")
        brooklyn_iid = self.put(state, brooklyn, 0, "play")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(6):
            self.put(state, ink_card, 0, "inkwell")

        activate = next(
            action for action in legal_actions(state)
            if action.get("kind") == "activate_ability"
            and action.get("iid") == brooklyn_iid
            and action.get("ability_name") == "WILD RIDE"
        )
        apply_action(state, activate)
        self.assertEqual(1, state.players[0].lore)

    def test_generic_action_puts_top_card_into_exerted_inkwell(self):
        state = self.make_state()
        card = self.db.find_by_full_name("One Jump Ahead")
        action_iid = self.put(state, card, 0, "hand")
        top_card = next(candidate for candidate in self.db.all_cards() if candidate.inkwell)
        top_iid = self.put(state, top_card, 0, "deck")
        ink_card = next(candidate for candidate in self.db.all_cards() if candidate.inkwell)
        for _ in range(card.cost):
            self.put(state, ink_card, 0, "inkwell")

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_action"
            and action.get("hand_iid") == action_iid and action.get("pay") == "ink"
        )
        apply_action(state, play)
        self.assertIn(top_iid, state.players[0].inkwell)
        self.assertTrue(state.instances[top_iid].exerted)

    def test_deck_four_has_a_resolver_for_every_unique_card(self):
        deck = load_decklist(self.db, "data/decklists/deck4.txt")
        self.assertEqual(19, len(set(deck)))
        unresolved = {self.db.get(cid).full_name: card_effect_gaps(self.db.get(cid)) for cid in set(deck)}
        self.assertEqual({}, {name: gaps for name, gaps in unresolved.items() if gaps})

    def test_angel_good_aim_is_once_per_turn_and_requires_a_real_effect(self):
        state = self.make_state()
        angel = self.db.find_by_full_name("Angel - Experiment 624")
        angel_iid = self.put(state, angel, 0, "play")
        fodder = next(c for c in self.db.all_cards() if c.inkwell)
        self.put(state, fodder, 0, "hand")
        self.put(state, fodder, 0, "hand")
        enemy = next(c for c in self.db.all_cards() if c.type == "Character" and not c.has_keyword("Ward") and not c.has_keyword("Resist"))
        enemy_iid = self.put(state, enemy, 1, "play")
        self.assertTrue(any(a.get("ability_name") == "GOOD AIM" for a in legal_actions(state)))
        state.decision_maker = lambda st, pidx, kind, options: (
            choose_discard(st, options) if kind == "discard_choice" else
            enemy_iid if enemy_iid in options else options[0]
        )
        activate = next(a for a in legal_actions(state) if a.get("kind") == "activate_ability" and a.get("iid") == angel_iid and a.get("ability_name") == "GOOD AIM")
        apply_action(state, activate)
        self.assertEqual(2, state.instances[enemy_iid].damage)
        self.assertFalse(any(a.get("ability_name") == "GOOD AIM" for a in legal_actions(state)))
        state.players[0].turns_started += 1
        self.assertTrue(any(a.get("ability_name") == "GOOD AIM" for a in legal_actions(state)))

    def test_angela_grants_temporary_challenger_and_resist_then_expires(self):
        state = self.make_state()
        angela = self.db.find_by_full_name("Angela - Night Warrior")
        angela_iid = self.put(state, angela, 0, "play")
        target_card = next(c for c in self.db.all_cards() if c.type == "Character" and not c.has_keyword("Challenger") and not c.has_keyword("Resist") and not c.has_keyword("Ward"))
        target_iid = self.put(state, target_card, 0, "play")
        state.decision_maker = lambda st, pidx, kind, options: target_iid if kind.startswith("angela_challenger_resist_target:") and target_iid in options else options[0]
        handler = engine.TRIGGERED_ABILITY_HANDLERS[("Angela - Night Warrior", "on_play")]
        handler(state, angela_iid)
        self.assertEqual((target_card.strength or 0) + 2, engine._effective_strength(state, target_iid, is_challenger=True))
        self.assertEqual(1, engine._damage_after_resist(state, target_iid, 3))
        clone = state.clone()
        self.assertEqual(state.delayed_challenger_mods, clone.delayed_challenger_mods)
        self.assertEqual(state.delayed_resist_mods, clone.delayed_resist_mods)
        _run_start_of_turn(state, skip_draw=True)
        self.assertEqual(3, engine._damage_after_resist(state, target_iid, 3))
        self.assertEqual(target_card.strength or 0, engine._effective_strength(state, target_iid, is_challenger=True))

    def test_angela_suppresses_demona_stone_by_day_for_gargoyles(self):
        state = self.make_state()
        angela = self.db.find_by_full_name("Angela - Night Warrior")
        demona = self.db.find_by_full_name("Demona - Scourge of the Wyvern Clan")
        self.put(state, angela, 0, "play")
        demona_iid = self.put(state, demona, 0, "play")
        fodder = next(c for c in self.db.all_cards() if c.inkwell)
        for _ in range(3):
            self.put(state, fodder, 0, "hand")
        blocker = engine.STATIC_READY_BLOCKERS["Demona - Scourge of the Wyvern Clan"]
        self.assertFalse(blocker(state, demona_iid))

    def test_deck_four_song_effects_resolve_damage_and_challenger_bonus(self):
        state = self.make_state()
        song = self.db.find_by_full_name("He Hurled His Thunderbolt")
        song_iid = self.put(state, song, 0, "hand")
        deity = next(c for c in self.db.all_cards() if c.type == "Character" and "Deity" in c.subtypes)
        deity_iid = self.put(state, deity, 0, "play")
        enemy = next(c for c in self.db.all_cards() if c.type == "Character" and not c.has_keyword("Ward") and not c.has_keyword("Resist"))
        enemy_iid = self.put(state, enemy, 1, "play")
        state.decision_maker = lambda st, pidx, kind, options: enemy_iid if enemy_iid in options else options[0]
        plan = engine._generic_action_plan(song)
        self.assertIsNotNone(plan)
        engine._resolve_generic_action_effect(state, song_iid, plan)
        self.assertEqual(4, state.instances[enemy_iid].damage)
        self.assertEqual((deity.strength or 0) + 2, engine._effective_strength(state, deity_iid, is_challenger=True))

        count_song = self.db.find_by_full_name("Strength of a Raging Fire")
        count_iid = self.put(state, count_song, 0, "hand")
        own_extra = next(c for c in self.db.all_cards() if c.type == "Character")
        self.put(state, own_extra, 0, "play")
        count_plan = engine._generic_action_plan(count_song)
        engine._resolve_generic_action_effect(state, count_iid, count_plan)
        self.assertEqual(6, state.instances[enemy_iid].damage)

    def test_terror_damage_uses_darkwing_substitution_amount(self):
        state = self.make_state()
        terror = self.db.find_by_full_name("The Terror That Flaps in the Night")
        terror_iid = self.put(state, terror, 0, "hand")
        darkwing = self.db.find_by_full_name("Darkwing Duck - Crime Fighter")
        self.put(state, darkwing, 0, "play")
        enemy = next(c for c in self.db.all_cards() if c.type == "Character" and not c.has_keyword("Ward") and not c.has_keyword("Resist"))
        enemy_iid = self.put(state, enemy, 1, "play")
        state.decision_maker = lambda st, pidx, kind, options: enemy_iid if enemy_iid in options else options[0]
        plan = engine._generic_action_plan(terror)
        engine._resolve_generic_action_effect(state, terror_iid, plan)
        self.assertEqual(3, state.instances[enemy_iid].damage)

    def test_launchpad_activated_ability_draws_then_discards_without_darkwing(self):
        state = self.make_state()
        launchpad = self.db.find_by_full_name("Launchpad - Trusty Sidekick")
        launchpad_iid = self.put(state, launchpad, 0, "play")
        fodder = next(c for c in self.db.all_cards() if c.inkwell)
        self.put(state, fodder, 0, "hand")
        drawn = next(c for c in self.db.all_cards() if c.inkwell)
        self.put(state, drawn, 0, "deck")
        state.decision_maker = lambda st, pidx, kind, options: choose_discard(st, options) if kind == "discard_choice" else options[0]
        activate = next(a for a in legal_actions(state) if a.get("kind") == "activate_ability" and a.get("iid") == launchpad_iid)
        apply_action(state, activate)
        self.assertEqual(1, len(state.players[0].hand))
        self.assertTrue(state.instances[launchpad_iid].exerted)
        self.assertEqual(1, len(state.players[0].discard))

    def test_generic_action_resolves_damage_to_a_chosen_character(self):
        state = self.make_state()
        action_card = self.db.find_by_full_name("Smash")
        action_iid = self.put(state, action_card, 0, "hand")
        target_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and (card.willpower or 0) >= 4
            and card.keyword_value("Resist") == 1 and not card.has_keyword("Ward")
        )
        target_iid = self.put(state, target_card, 1, "play")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(action_card.cost):
            self.put(state, ink_card, 0, "inkwell")

        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_action"
            and action.get("hand_iid") == action_iid and action.get("pay") == "ink"
        )
        apply_action(state, play)
        self.assertEqual(2, state.instances[target_iid].damage)
        self.assertIn(action_iid, state.players[0].discard)

    def test_heuristic_and_search_choose_lethal_enemy_damage_targets(self):
        state = self.make_state()
        own_card = next(card for card in self.db.all_cards() if card.type == "Character")
        weak_enemy = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and 0 < (card.willpower or 0) <= 2
            and not card.has_keyword("Ward") and not card.has_keyword("Resist")
        )
        tough_enemy = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and (card.willpower or 0) >= 5
            and not card.has_keyword("Ward") and not card.has_keyword("Resist")
        )
        own_iid = self.put(state, own_card, 0, "play")
        weak_iid = self.put(state, weak_enemy, 1, "play")
        tough_iid = self.put(state, tough_enemy, 1, "play")
        resistant_enemy = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and (card.willpower or 0) >= 5
            and card.keyword_value("Resist") == 2 and not card.has_keyword("Ward")
        )
        resistant_iid = self.put(state, resistant_enemy, 1, "play")
        state.instances[resistant_iid].damage = (resistant_enemy.willpower or 0) - 2
        options = [own_iid, tough_iid, weak_iid, resistant_iid]
        kind = "action_damage_target:0:2"

        self.assertEqual(weak_iid, HeuristicBot(random.Random(5)).choose_target(state, kind, options))
        self.assertEqual(weak_iid, SearchBot(random.Random(5)).choose_target(state, kind, options))

    def test_generic_simple_challenge_banish_trigger_gives_ink_drop(self):
        state = self.make_state()
        owl = self.db.find_by_full_name("Archimedes - Messenger Owl")
        owl_iid = self.put(state, owl, 1, "play")
        state.instances[owl_iid].exerted = True
        attacker_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and (card.strength or 0) >= (owl.willpower or 0)
            and not card.has_keyword("Adventurous")
        )
        attacker_iid = self.put(state, attacker_card, 0, "play")
        challenge = next(
            action for action in legal_actions(state)
            if action.get("kind") == "challenge" and action.get("defender_iid") == owl_iid
        )
        apply_action(state, challenge)
        self.assertEqual(1, state.players[1].ink_drops)
        self.assertIn(owl_iid, state.players[1].discard)

    def test_drying_expires_at_owner_next_turn_not_opponents_turn(self):
        state = self.make_state()
        card = next(c for c in self.db.all_cards() if c.type == "Character")
        iid = self.put(state, card, 0, "play")
        state.instances[iid].entered_play_turn = 2
        state.players[0].turns_started = 2
        state.active_idx = 1
        state.turn_number = 3
        self.assertFalse(state.is_dry(iid))
        state.players[0].turns_started += 1
        self.assertTrue(state.is_dry(iid))


    def test_sing_together_offers_subsets_and_only_exerts_selected_singers(self):
        state = self.make_state()
        song = next(
            c for c in self.db.all_cards()
            if "Song" in c.subtypes and any(a.keyword == "Sing Together" and a.keyword_value_number == 7 for a in c.abilities)
        )
        song_iid = self.put(state, song, 0, "hand")
        singers = {}
        for cost in (2, 3, 4):
            card = next(
                c for c in self.db.all_cards()
                if c.type == "Character" and c.cost == cost and not c.has_keyword("Singer")
            )
            singers[cost] = self.put(state, card, 0, "play")

        actions = [
            action for action in legal_actions(state)
            if action.get("hand_iid") == song_iid and action.get("pay") == "sing_together"
        ]
        groups = {tuple(action["singers"]) for action in actions}
        self.assertIn((singers[3], singers[4]), groups)
        self.assertIn((singers[2], singers[3], singers[4]), groups)
        self.assertNotIn((singers[2], singers[4]), groups)

        chosen = next(action for action in actions if tuple(action["singers"]) == (singers[3], singers[4]))
        apply_action(state, chosen)
        self.assertTrue(state.instances[singers[3]].exerted)
        self.assertTrue(state.instances[singers[4]].exerted)
        self.assertFalse(state.instances[singers[2]].exerted)

    def test_alert_evasive_and_bodyguard_challenge_limits(self):
        normal = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and not any(
                card.has_keyword(keyword)
                for keyword in ("Evasive", "Alert", "Adventurous", "Reckless")
            )
        )
        alert = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.has_keyword("Alert")
            and not card.has_keyword("Evasive") and not card.has_keyword("Adventurous")
        )
        evasive = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.has_keyword("Evasive")
        )
        state = self.make_state()
        normal_iid = self.put(state, normal, 0, "play")
        alert_iid = self.put(state, alert, 0, "play")
        evasive_iid = self.put(state, evasive, 1, "play")
        state.instances[evasive_iid].exerted = True
        challenges = [
            action for action in legal_actions(state)
            if action.get("kind") == "challenge" and action.get("defender_iid") == evasive_iid
        ]
        self.assertFalse(any(action["attacker_iid"] == normal_iid for action in challenges))
        self.assertTrue(any(action["attacker_iid"] == alert_iid for action in challenges))

        bodyguard = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.has_keyword("Bodyguard")
            and not card.has_keyword("Evasive")
        )
        ordinary = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and not card.has_keyword("Bodyguard")
            and not card.has_keyword("Evasive") and not card.has_keyword("Ward")
        )
        state = self.make_state()
        attacker_iid = self.put(state, normal, 0, "play")
        bodyguard_iid = self.put(state, bodyguard, 1, "play")
        ordinary_iid = self.put(state, ordinary, 1, "play")
        state.instances[bodyguard_iid].exerted = True
        state.instances[ordinary_iid].exerted = True
        forced_targets = {
            action["defender_iid"] for action in legal_actions(state)
            if action.get("kind") == "challenge" and action["attacker_iid"] == attacker_iid
        }
        self.assertEqual({bodyguard_iid}, forced_targets)

    def test_rush_and_reckless_challenge_restrictions(self):
        rush = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.has_keyword("Rush")
            and not card.has_keyword("Reckless") and not card.has_keyword("Adventurous")
        )
        ordinary = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and not card.has_keyword("Evasive")
            and not card.has_keyword("Ward") and not card.has_keyword("Bodyguard")
        )
        state = self.make_state()
        rush_iid = self.put(state, rush, 0, "hand")
        target_iid = self.put(state, ordinary, 1, "play")
        state.instances[target_iid].exerted = True
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(rush.cost):
            self.put(state, ink_card, 0, "inkwell")
        play = next(
            action for action in legal_actions(state)
            if action.get("kind") == "play_character" and action.get("hand_iid") == rush_iid
            and action.get("pay") == "ink"
        )
        apply_action(state, play)
        actions = legal_actions(state)
        self.assertFalse(any(action.get("kind") == "quest" and action.get("iid") == rush_iid for action in actions))
        self.assertTrue(any(
            action.get("kind") == "challenge" and action.get("attacker_iid") == rush_iid
            for action in actions
        ))

        reckless = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.has_keyword("Reckless")
            and not card.has_keyword("Adventurous")
        )
        state = self.make_state()
        reckless_iid = self.put(state, reckless, 0, "play")
        target_iid = self.put(state, ordinary, 1, "play")
        state.instances[target_iid].exerted = True
        actions = legal_actions(state)
        self.assertFalse(any(action.get("kind") == "quest" and action.get("iid") == reckless_iid for action in actions))
        self.assertFalse(any(action.get("kind") == "end_turn" for action in actions))
        challenge = next(
            action for action in actions
            if action.get("kind") == "challenge" and action.get("attacker_iid") == reckless_iid
        )
        apply_action(state, challenge)
        self.assertTrue(any(action.get("kind") == "end_turn" for action in legal_actions(state)))

    def test_challenger_bonus_and_resist_apply_in_challenge_damage(self):
        attacker_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and (card.keyword_value("Challenger") or 0) > 0
            and (card.willpower or 0) >= 5 and not card.has_keyword("Adventurous")
        )
        defender_card = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and (card.willpower or 0) >= 8
            and (card.keyword_value("Resist") or 0) > 0
            and not card.has_keyword("Evasive") and not card.has_keyword("Ward")
        )
        state = self.make_state()
        attacker_iid = self.put(state, attacker_card, 0, "play")
        defender_iid = self.put(state, defender_card, 1, "play")
        state.instances[defender_iid].exerted = True
        challenge = next(
            action for action in legal_actions(state)
            if action.get("kind") == "challenge" and action.get("attacker_iid") == attacker_iid
            and action.get("defender_iid") == defender_iid
        )
        expected_damage = max(
            (attacker_card.strength or 0) + (attacker_card.keyword_value("Challenger") or 0)
            - (defender_card.keyword_value("Resist") or 0),
            0,
        )
        apply_action(state, challenge)
        self.assertEqual(expected_damage, state.instances[defender_iid].damage)

    def test_dumbo_grants_activated_ability_to_other_evasive_characters(self):
        state = self.make_state()
        dumbo = self.db.find_by_full_name("Dumbo - Ninth Wonder of the Universe")
        evasive = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.has_keyword("Evasive") and card.id != dumbo.id
        )
        ordinary = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and not card.has_keyword("Evasive")
        )
        dumbo_iid = self.put(state, dumbo, 0, "play")
        evasive_iid = self.put(state, evasive, 0, "play")
        ordinary_iid = self.put(state, ordinary, 0, "play")
        draw_card = next(card for card in self.db.all_cards() if card.inkwell)
        draw_iid = self.put(state, draw_card, 0, "deck")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        self.put(state, ink_card, 0, "inkwell")

        actions = legal_actions(state)
        making_history = next(
            action for action in actions
            if action.get("kind") == "activate_ability" and action.get("iid") == evasive_iid
            and action.get("ability_name") == "MAKING HISTORY"
            and action.get("granted_by") == dumbo_iid
        )
        self.assertFalse(any(
            action.get("kind") == "activate_ability" and action.get("iid") == ordinary_iid
            and action.get("ability_name") == "MAKING HISTORY"
            for action in actions
        ))
        apply_action(state, making_history)

        self.assertTrue(state.instances[evasive_iid].exerted)
        self.assertEqual(1, state.players[0].lore)
        self.assertIn(draw_iid, state.players[0].hand)
    def test_support_and_boost_resolve_once_per_turn(self):
        supporter = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.has_keyword("Support") and (card.strength or 0) > 0
        )
        other = next(card for card in self.db.all_cards() if card.type == "Character" and card.id != supporter.id)
        state = self.make_state()
        supporter_iid = self.put(state, supporter, 0, "play")
        other_iid = self.put(state, other, 0, "play")
        state.decision_maker = lambda _st, _pidx, _kind, options: other_iid if other_iid in options else options[0]
        quest = next(action for action in legal_actions(state) if action.get("kind") == "quest" and action.get("iid") == supporter_iid)
        apply_action(state, quest)
        self.assertEqual(supporter.strength, state.temp_strength_bonus[other_iid])

        booster = next(
            card for card in self.db.all_cards()
            if card.type == "Character" and card.has_keyword("Boost")
        )
        state = self.make_state()
        booster_iid = self.put(state, booster, 0, "play")
        top_card = next(card for card in self.db.all_cards() if card.inkwell)
        top_iid = self.put(state, top_card, 0, "deck")
        ink_card = next(card for card in self.db.all_cards() if card.inkwell)
        for _ in range(booster.keyword_value("Boost") or 0):
            self.put(state, ink_card, 0, "inkwell")
        boost = next(
            action for action in legal_actions(state)
            if action.get("kind") == "boost" and action.get("iid") == booster_iid
        )
        apply_action(state, boost)
        self.assertIn(top_iid, state.players[0].under)
        self.assertFalse(any(
            action.get("kind") == "boost" and action.get("iid") == booster_iid
            for action in legal_actions(state)
        ))
    def test_ward_blocks_opponent_choices_but_not_non_target_effects(self):
        state = self.make_state()
        warded = next(c for c in self.db.all_cards() if c.type == "Character" and c.has_keyword("Ward"))
        ordinary = next(c for c in self.db.all_cards() if c.type == "Character" and not c.has_keyword("Ward"))
        warded_iid = self.put(state, warded, 1, "play")
        ordinary_iid = self.put(state, ordinary, 1, "play")
        state.decision_maker = lambda _state, _pidx, _kind, options: options[0]

        self.assertEqual(ordinary_iid, state.choose(0, "test_target", [warded_iid, ordinary_iid]))
        self.assertEqual(warded_iid, state.choose(1, "own_target", [warded_iid]))
        deal_damage(state, warded_iid, 1)
        self.assertEqual(1, state.instances[warded_iid].damage)


    def test_setup_mulligan_replaces_selected_cards_in_starting_player_order(self):
        card = next(c for c in self.db.all_cards() if c.inkwell)
        deck = [card.id] * 20
        calls = []

        def replace_one(state, pidx, hand):
            calls.append(pidx)
            return hand[:1]

        state = new_game(
            self.db, deck, deck,
            config=GameConfig(starting_hand_size=4, allow_mulligan=True),
            seed=19, mulligan_decider=replace_one,
        )
        self.assertEqual([state.active_idx, state.opponent_idx(state.active_idx)], calls)
        self.assertEqual([4, 4], [len(player.hand) for player in state.players])
        self.assertTrue(all(len(player.deck) == 16 for player in state.players))
        self.assertTrue(all(player.turns_started == (1 if idx == state.active_idx else 0) for idx, player in enumerate(state.players)))

    def test_challenge_banishment_queues_challenge_specific_trigger(self):
        state = self.make_state()
        attacker = next(
            c for c in self.db.all_cards()
            if c.type == "Character" and c.strength and c.strength > 0
            and not c.has_keyword("Reckless") and not c.has_keyword("Adventurous")
            and not c.has_keyword("Challenger")
            and c.full_name not in engine.STATIC_DAMAGE_SOURCE_OVERRIDE
        )
        defender = next(
            c for c in self.db.all_cards()
            if c.type == "Character" and c.willpower
            and c.willpower <= (attacker.strength or 0)
            and (c.strength is None or c.strength < (attacker.willpower or 0))
            and not c.has_keyword("Evasive") and not c.has_keyword("Resist")
        )
        attacker_iid = self.put(state, attacker, 0, "play")
        defender_iid = self.put(state, defender, 1, "play")
        state.instances[defender_iid].exerted = True
        key = (defender.full_name, "on_banish_in_challenge")
        observed = []
        engine.TRIGGERED_ABILITY_HANDLERS[key] = (
            lambda _st, iid, **_ctx: observed.append(iid)
        )
        action = next(
            a for a in legal_actions(state)
            if a.get("kind") == "challenge" and a.get("attacker_iid") == attacker_iid
            and a.get("defender_iid") == defender_iid
        )
        try:
            apply_action(state, action)
        finally:
            engine.TRIGGERED_ABILITY_HANDLERS.pop(key, None)

        self.assertIn(defender_iid, state.players[1].discard)
        self.assertEqual([defender_iid], observed)

    def test_removing_ink_drops_queues_registered_triggers(self):
        state = self.make_state()
        source = next(c for c in self.db.all_cards() if c.type == "Character")
        iid = self.put(state, source, 0, "play")
        state.players[0].ink_drops = 2
        calls = []
        key = (source.full_name, "on_ink_drop_removed")
        engine.TRIGGERED_ABILITY_HANDLERS[key] = lambda _st, source_iid, **_ctx: calls.append(source_iid)
        try:
            engine.remove_ink_drops(state, 0, 2)
            self.assertEqual(2, len(state.triggered_bag))
            _resolve_trigger_bag(state)
        finally:
            engine.TRIGGERED_ABILITY_HANDLERS.pop(key, None)

        self.assertEqual([iid, iid], calls)
        self.assertEqual(0, state.players[0].ink_drops)

    def test_moving_to_location_queues_its_trigger(self):
        state = self.make_state()
        character = next(c for c in self.db.all_cards() if c.type == "Character")
        location = next(c for c in self.db.all_cards() if c.type == "Location")
        char_iid = self.put(state, character, 0, "play")
        location_iid = self.put(state, location, 0, "play")
        cost = location.move_cost or 0
        state.players[0].ink_drops = cost
        calls = []
        key = (location.full_name, "on_character_moves_here")
        engine.TRIGGERED_ABILITY_HANDLERS[key] = (
            lambda _st, _iid, source_iid, **_ctx: calls.append(source_iid)
        )
        try:
            action = next(
                a for a in legal_actions(state)
                if a.get("kind") == "move" and a.get("char_iid") == char_iid
                and a.get("location_iid") == location_iid
            )
            apply_action(state, action)
        finally:
            engine.TRIGGERED_ABILITY_HANDLERS.pop(key, None)

        self.assertEqual(location_iid, state.instances[char_iid].at_location)
        self.assertEqual([char_iid], calls)

    def test_end_of_turn_triggers_resolve_before_turn_changes(self):
        state = self.make_state()
        item = next(c for c in self.db.all_cards() if c.type == "Item")
        iid = self.put(state, item, 0, "play")
        deck_card = next(c for c in self.db.all_cards() if c.inkwell)
        self.put(state, deck_card, 0, "deck")
        seen = []
        key = (item.full_name, "on_end_of_turn")
        engine.TRIGGERED_ABILITY_HANDLERS[key] = (
            lambda st, source_iid, **_ctx: seen.append((source_iid, st.active_idx, st.phase))
        )
        try:
            _run_end_of_turn(state)
        finally:
            engine.TRIGGERED_ABILITY_HANDLERS.pop(key, None)

        self.assertEqual([(iid, 0, "end_of_turn")], seen)
        self.assertEqual(1, state.active_idx)

    def test_lore_win_from_end_trigger_precedes_empty_deck_loss(self):
        state = self.make_state()
        item = next(card for card in self.db.all_cards() if card.type == "Item")
        iid = self.put(state, item, 0, "play")
        state.players[0].lore = 19
        state.players[0].deck.clear()
        key = (item.full_name, "on_end_of_turn")
        engine.TRIGGERED_ABILITY_HANDLERS[key] = (
            lambda st, _source_iid, **_ctx: setattr(st.players[0], "lore", 20)
        )
        try:
            _run_end_of_turn(state)
        finally:
            engine.TRIGGERED_ABILITY_HANDLERS.pop(key, None)

        self.assertEqual(0, state.winner)
        self.assertEqual("game_over", state.phase)

    def test_empty_deck_loses_at_its_end_of_turn(self):
        state = self.make_state()
        state.players[0].deck.clear()
        apply_action(state, {"kind": "end_turn"})
        self.assertEqual(1, state.winner)
        self.assertEqual("game_over", state.phase)

    def test_leaving_play_location_unassigns_characters(self):
        state = self.make_state()
        location = next(c for c in self.db.all_cards() if c.type == "Location")
        character = next(c for c in self.db.all_cards() if c.type == "Character")
        location_iid = self.put(state, location, 0, "play")
        character_iid = self.put(state, character, 0, "play")
        state.instances[character_iid].at_location = location_iid

        engine.banish(state, location_iid)

        self.assertIsNone(state.instances[character_iid].at_location)
        self.assertIn(location_iid, state.players[0].discard)

    def test_trigger_bag_uses_active_player_order_and_allows_controller_order(self):
        state = self.make_state()
        cards = [next(c for c in self.db.all_cards() if c.type == "Character") for _ in range(2)]
        source_iids = [self.put(state, cards[pidx], pidx, "play") for pidx in (0, 1)]
        resolved = []
        choices = []

        def make_handler(label):
            def handler(_state, _iid, **_ctx):
                resolved.append(label)
            return handler

        engine._queue_trigger(state, source_iids[0], make_handler("p0 first"), "p0_first")
        engine._queue_trigger(state, source_iids[0], make_handler("p0 second"), "p0_second")
        engine._queue_trigger(state, source_iids[1], make_handler("p1"), "p1")

        def choose_order(_state, pidx, kind, options):
            self.assertEqual("trigger_order", kind)
            choices.append((pidx, [trigger["event"] for trigger in options]))
            if pidx == 0:
                return options[-1]
            return options[0]

        state.decision_maker = choose_order
        _resolve_trigger_bag(state)

        self.assertEqual(["p0 second", "p0 first", "p1"], resolved)
        self.assertEqual([0, 0, 1], [pidx for pidx, _ in choices])
        self.assertEqual({"p0_first", "p0_second"}, set(choices[0][1]))

    def test_item_can_listen_for_an_ally_quest(self):
        state = self.make_state()
        character = next(c for c in self.db.all_cards() if c.type == "Character" and c.lore)
        item = next(c for c in self.db.all_cards() if c.type == "Item")
        char_iid = self.put(state, character, 0, "play")
        item_iid = self.put(state, item, 0, "play")
        calls = []
        key = (item.full_name, "on_ally_quest")
        engine.TRIGGERED_ABILITY_HANDLERS[key] = (
            lambda _st, listener_iid, source_iid, **_ctx: calls.append(
                (listener_iid, source_iid)
            )
        )
        try:
            apply_action(state, {"kind": "quest", "iid": char_iid})
        finally:
            engine.TRIGGERED_ABILITY_HANDLERS.pop(key, None)

        self.assertEqual([(item_iid, char_iid)], calls)
        self.assertIn(item_iid, state.players[0].play)

    def test_start_and_ready_triggers_wait_until_after_set_and_include_items(self):
        state = self.make_state()
        character = next(c for c in self.db.all_cards() if c.type == "Character")
        item = next(c for c in self.db.all_cards() if c.type == "Item")
        char_iid = self.put(state, character, 0, "play")
        item_iid = self.put(state, item, 0, "play")
        state.instances[char_iid].exerted = True
        state.players[0].inked_this_turn = True
        draw_card = next(c for c in self.db.all_cards() if c.inkwell)
        self.put(state, draw_card, 0, "deck")
        observed = []
        decision_options = []

        def on_start(st, iid, **_ctx):
            observed.append((
                "start", iid, st.instances[char_iid].exerted,
                st.players[0].turns_started, len(st.players[0].deck),
            ))

        def on_ready(st, iid, **_ctx):
            observed.append((
                "ready", iid, st.instances[iid].exerted,
                st.players[0].turns_started, len(st.players[0].deck),
            ))

        keys = [
            (character.full_name, "on_start_of_turn"),
            (character.full_name, "on_ready"),
            (item.full_name, "on_start_of_turn"),
        ]
        engine.TRIGGERED_ABILITY_HANDLERS[keys[0]] = on_start
        engine.TRIGGERED_ABILITY_HANDLERS[keys[1]] = on_ready
        engine.TRIGGERED_ABILITY_HANDLERS[keys[2]] = on_start
        state.decision_maker = lambda _st, _pidx, _kind, options: (
            decision_options.append({trigger["event"] for trigger in options}) or options[0]
        )
        try:
            _run_start_of_turn(state)
        finally:
            for key in keys:
                engine.TRIGGERED_ABILITY_HANDLERS.pop(key, None)

        self.assertEqual(3, state.players[0].turns_started)
        self.assertFalse(state.players[0].inked_this_turn)
        self.assertFalse(state.instances[char_iid].exerted)
        self.assertTrue(any("on_start_of_turn" in events and "on_ready" in events for events in decision_options))
        self.assertEqual(3, len(observed))
        self.assertTrue(all(entry[2] is False and entry[3] == 3 and entry[4] == 1 for entry in observed))
        self.assertEqual(0, len(state.players[0].deck))
        self.assertEqual(1, len(state.players[0].hand))

    def test_challenge_declaration_triggers_resolve_before_challenge_damage(self):
        state = self.make_state()
        attacker = next(
            c for c in self.db.all_cards()
            if c.type == "Character" and c.strength and c.strength > 0
            and not c.has_keyword("Reckless") and not c.has_keyword("Adventurous")
            and not c.has_keyword("Challenger")
            and c.full_name not in engine.STATIC_DAMAGE_SOURCE_OVERRIDE
        )
        defender = next(
            c for c in self.db.all_cards()
            if c.type == "Character" and c.willpower
            and c.willpower >= (attacker.strength or 0) + 3
            and (c.strength is None or c.strength < (attacker.willpower or 0))
            and not c.has_keyword("Evasive") and not c.has_keyword("Resist")
        )
        attacker_iid = self.put(state, attacker, 0, "play")
        defender_iid = self.put(state, defender, 1, "play")
        state.instances[defender_iid].exerted = True
        baseline = attacker.strength or 0
        key = (attacker.full_name, "on_challenge")
        engine.TRIGGERED_ABILITY_HANDLERS[key] = lambda st, iid, **_ctx: st.temp_strength_bonus.__setitem__(iid, 2)
        action = next(
            a for a in legal_actions(state)
            if a.get("kind") == "challenge" and a.get("attacker_iid") == attacker_iid and a.get("defender_iid") == defender_iid
        )
        try:
            apply_action(state, action)
        finally:
            engine.TRIGGERED_ABILITY_HANDLERS.pop(key, None)

        self.assertEqual(baseline + 2, state.instances[defender_iid].damage)

    def test_state_clone_preserves_ink_drops(self):
        state = self.make_state()
        state.players[0].ink_drops = 3
        state.first_player_idx = 1
        state.once_per_turn_trigger_usage[(7, "on_play_action")] = 2
        cloned = state.clone()
        self.assertEqual(3, cloned.players[0].ink_drops)
        self.assertEqual(1, cloned.first_player_idx)
        self.assertEqual(2, cloned.once_per_turn_trigger_usage[(7, "on_play_action")])
        cloned.once_per_turn_trigger_usage[(7, "on_play_action")] = 3
        self.assertEqual(2, state.once_per_turn_trigger_usage[(7, "on_play_action")])
        cloned.players[0].ink_drops -= 1
        self.assertEqual(3, state.players[0].ink_drops)
        state.players[0].turns_started = 4
        self.assertEqual(2, cloned.players[0].turns_started)

    def test_heuristic_uses_generic_removal_only_when_it_can_help(self):
        db = self.db
        smash = db.find_by_full_name("Smash")
        ink_card = next(card for card in db.all_cards() if card.inkwell)
        useful_target = next(
            card for card in db.all_cards()
            if card.type == "Character" and 0 < (card.willpower or 0) <= 3
            and not card.has_keyword("Ward") and not card.has_keyword("Resist")
        )
        state = self.make_state()
        smash_iid = self.put(state, smash, 0, "hand")
        self.put(state, useful_target, 1, "play")
        for _ in range(smash.cost):
            self.put(state, ink_card, 0, "inkwell")

        chosen = HeuristicBot(random.Random(8)).choose(state)
        self.assertEqual("play_action", chosen.get("kind"))
        self.assertEqual(smash_iid, chosen.get("hand_iid"))

        state = self.make_state()
        smash_iid = self.put(state, smash, 0, "hand")
        own_target = self.put(state, useful_target, 0, "play")
        state.instances[own_target].exerted = True
        for _ in range(smash.cost):
            self.put(state, ink_card, 0, "inkwell")

        chosen = HeuristicBot(random.Random(8)).choose(state)
        self.assertFalse(chosen.get("kind") == "play_action" and chosen.get("hand_iid") == smash_iid)

    def test_ink_drops_can_mix_with_ink_cards_to_pay(self):
        state = self.make_state()
        card = next(
            c for c in self.db.all_cards()
            if c.set_number == 14 and c.cost == 3 and c.inkwell and c.type != "Action"
        )
        hand_iid = self.put(state, card, 0, "hand")
        ink_iids = [
            self.put(state, card, 0, "inkwell"),
            self.put(state, card, 0, "inkwell"),
        ]
        state.players[0].ink_drops = 2

        actions = [
            action for action in legal_actions(state)
            if action.get("hand_iid") == hand_iid and action["kind"] in ("play_character", "play_item", "play_location")
        ]
        self.assertEqual({1, 2}, {action.get("ink_drops", 0) for action in actions})

        action = next(action for action in actions if action.get("ink_drops") == 2)
        apply_action(state, action)
        self.assertEqual(0, state.players[0].ink_drops)
        self.assertEqual(1, sum(state.instances[iid].exerted for iid in ink_iids))

    def test_adventurous_character_must_quest_and_cannot_challenge(self):
        state = self.make_state()
        adventurous = self.db.find_by_full_name("Mickey Mouse - Best in Town")
        target = next(c for c in self.db.all_cards() if c.type == "Character" and c.full_name != adventurous.full_name)
        iid = self.put(state, adventurous, 0, "play")
        target_iid = self.put(state, target, 1, "play")
        state.instances[target_iid].exerted = True

        actions = legal_actions(state)
        self.assertIn({"kind": "quest", "iid": iid}, actions)
        self.assertNotIn(
            {"kind": "challenge", "attacker_iid": iid, "defender_iid": target_iid, "target_type": "character"},
            actions,
        )
        self.assertNotIn({"kind": "end_turn"}, actions)
        with self.assertRaises(ValueError):
            apply_action(state, {"kind": "end_turn"})

        apply_action(state, {"kind": "quest", "iid": iid})
        self.assertTrue(state.instances[iid].exerted)
        self.assertIn({"kind": "end_turn"}, legal_actions(state))


    def test_duo_shift_needs_two_names_and_combines_state(self):
        state = self.make_state()
        duo = self.db.find_by_full_name("Mickey Mouse & Minnie Mouse - Adventuring Duo")
        mickey = self.db.find_by_full_name("Mickey Mouse - Inquisitive Explorer")
        minnie = self.db.find_by_full_name("Minnie Mouse - Curious Adventurer")
        duo_iid = self.put(state, duo, 0, "hand")
        mickey_iid = self.put(state, mickey, 0, "play")
        minnie_iid = self.put(state, minnie, 0, "play")
        location = next(c for c in self.db.all_cards() if c.type == "Location")
        location_iid = self.put(state, location, 0, "play")
        state.instances[mickey_iid].at_location = location_iid
        state.instances[minnie_iid].at_location = location_iid
        state.instances[mickey_iid].damage = 1
        state.instances[minnie_iid].damage = 2
        state.instances[minnie_iid].entered_play_turn = state.turn_number
        state.instances[minnie_iid].exerted = True
        state.temp_strength_bonus[mickey_iid] = 1
        state.temp_strength_bonus[minnie_iid] = 2
        state.delayed_strength_mods.extend([
            {"iid": mickey_iid, "amount": 1, "owner": 0},
            {"iid": minnie_iid, "amount": 2, "owner": 0},
        ])

        action = next(
            action for action in legal_actions(state)
            if action.get("hand_iid") == duo_iid and action.get("pay") == "shift"
        )
        self.assertEqual({mickey_iid, minnie_iid}, set(action["target_iids"]))
        apply_action(state, action)
        self.assertEqual(3, state.instances[duo_iid].damage)
        self.assertTrue(state.instances[duo_iid].exerted)
        self.assertFalse(state.is_dry(duo_iid))
        self.assertEqual({mickey_iid, minnie_iid}, set(state.instances[duo_iid].stacked_under))
        self.assertEqual(location_iid, state.instances[duo_iid].at_location)
        self.assertEqual(3, state.temp_strength_bonus[duo_iid])
        self.assertEqual(
            [1, 2],
            [mod["amount"] for mod in state.delayed_strength_mods if mod["iid"] == duo_iid],
        )


    def test_duo_shift_does_not_count_one_dual_named_character_twice(self):
        state = self.make_state()
        duo = self.db.find_by_full_name("Mickey Mouse & Minnie Mouse - Adventuring Duo")
        single = self.db.find_by_full_name("Mickey Mouse & Minnie Mouse - Adventuring Duo")
        hand_iid = self.put(state, duo, 0, "hand")
        self.put(state, single, 0, "play")
        shift_actions = [
            action for action in legal_actions(state)
            if action.get("hand_iid") == hand_iid and action.get("pay") == "shift"
        ]
        self.assertEqual([], shift_actions)

    def test_combo_shift_offers_either_name_or_both(self):
        state = self.make_state()
        combo = self.db.find_by_full_name("Sulley & Boo - Scare Buddies")
        sulley = self.db.find_by_full_name("Sulley - Protective Monster")
        boo = self.db.find_by_full_name("Boo - Energetic Child")
        combo_iid = self.put(state, combo, 0, "hand")
        sulley_iid = self.put(state, sulley, 0, "play")
        boo_iid = self.put(state, boo, 0, "play")
        state.players[0].ink_drops = 4

        actions = [
            action for action in legal_actions(state)
            if action.get("hand_iid") == combo_iid and action.get("pay") == "shift"
        ]
        targets = {
            tuple(action.get("target_iids", [action.get("target_iid")]))
            for action in actions
        }
        self.assertEqual(
            { (sulley_iid,), (boo_iid,), tuple(sorted((sulley_iid, boo_iid))) },
            {tuple(sorted(target)) for target in targets},
        )

    def test_temporary_shift_returns_and_clears_stack_damage(self):
        state = self.make_state()
        temporary = self.db.find_by_full_name("Meilin Lee - Popular Red Panda")
        base = self.db.find_by_full_name("Meilin Lee - Losing Control")
        temp_iid = self.put(state, temporary, 0, "hand")
        base_iid = self.put(state, base, 0, "play")
        state.players[0].ink_drops = 3

        action = next(
            action for action in legal_actions(state)
            if action.get("hand_iid") == temp_iid and action.get("pay") == "shift"
        )
        apply_action(state, action)
        self.assertIn(temp_iid, state.temporary_shift_iids)
        state.instances[temp_iid].damage = 2
        state.instances[temp_iid].exerted = True
        _return_temporary_shifts(state, 0)

        self.assertIn(temp_iid, state.players[0].hand)
        self.assertNotIn(temp_iid, state.temporary_shift_iids)
        self.assertEqual([base_iid], state.players[0].play)
        self.assertEqual(0, state.instances[base_iid].damage)
        self.assertTrue(state.instances[base_iid].exerted)

    def test_temporary_shift_returns_from_middle_of_stack(self):
        state = self.make_state()
        temporary = self.db.find_by_full_name("Meilin Lee - Popular Red Panda")
        base = self.db.find_by_full_name("Meilin Lee - Losing Control")
        top = self.db.find_by_full_name("Mickey Mouse - Inquisitive Explorer")
        base_iid = self.put(state, base, 0, "play")
        temp_iid = self.put(state, temporary, 0, "play")
        top_iid = self.put(state, top, 0, "play")
        state.instances[temp_iid].stacked_on = top_iid
        state.instances[base_iid].stacked_on = temp_iid
        state.instances[temp_iid].stacked_under = [base_iid]
        state.instances[top_iid].stacked_under = [temp_iid, base_iid]
        state.instances[top_iid].damage = 3
        state.temporary_shift_iids.add(temp_iid)

        _return_temporary_shifts(state, 0)

        self.assertIn(temp_iid, state.players[0].hand)
        self.assertEqual(top_iid, state.instances[base_iid].stacked_on)
        self.assertEqual([base_iid], state.instances[top_iid].stacked_under)
        self.assertEqual(0, state.instances[top_iid].damage)
        self.assertEqual({top_iid, base_iid}, set(state.players[0].play))

    def test_potato_shift_and_baymax_drop_cost(self):
        state = self.make_state()
        potato = next(c for c in self.db.all_cards() if c.name == "Potato" and c.type == "Item")
        posey = self.db.find_by_full_name("Posey - Vampire Potato")
        potato_iid = self.put(state, potato, 0, "play")
        state.instances[potato_iid].entered_play_turn = 1
        posey_iid = self.put(state, posey, 0, "hand")
        state.players[0].ink_drops = 5

        potato_action = next(
            action for action in legal_actions(state)
            if action.get("hand_iid") == posey_iid and action.get("target_iid") == potato_iid
        )
        apply_action(state, potato_action)
        self.assertTrue(state.is_dry(posey_iid))
        self.assertNotIn(potato_iid, state.characters_in_play(0))

        baymax_state = self.make_state()
        baymax = self.db.find_by_full_name("Baymax - Amped Up")
        baymax_base = next(c for c in self.db.all_cards() if c.name == "Baymax" and c.id != baymax.id)
        baymax_iid = self.put(baymax_state, baymax, 0, "hand")
        base_iid = self.put(baymax_state, baymax_base, 0, "play")
        baymax_state.players[0].ink_drops = 2
        drop_action = next(
            action for action in legal_actions(baymax_state)
            if action.get("hand_iid") == baymax_iid and action.get("target_iid") == base_iid
        )
        self.assertEqual(0, drop_action["shift_cost"])
        self.assertEqual(2, drop_action["shift_remove_drops"])
        apply_action(baymax_state, drop_action)
        self.assertEqual(0, baymax_state.players[0].ink_drops)



if __name__ == "__main__":
    unittest.main()