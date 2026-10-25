"""A step above RandomBot: cheap heuristics, no search.

Priority order per turn (re-evaluated after every action, since the legal
action list changes as state changes):
  1. Take challenges that banish the opponent's character without losing
     your own (rough strength/willpower comparison, ignoring most keyword
     modifiers for simplicity).
  2. Quest with characters that gain lore.
  3. Play the highest-cost card you can afford (curve out).
  4. Ink the lowest-cost inkable card in hand (keep better cards in hand).
  5. Move / anything else.
  6. End turn.

This is meant as a baseline to self-play against and to sanity-check the
engine — not a strong player. See the project README, roadmap step 5,
for where real search-based decision-making would replace this.
"""
from __future__ import annotations

import random

from lorcana.engine import (
    legal_actions, Action, ACTION_EFFECT_HANDLERS, _effective_strength, _damage_after_resist, _generic_action_plan,
)
from lorcana.state import GameState
from bots.targeting import (
    choose_banish_target, choose_bounce_target, choose_damage_target, choose_discard,
    choose_opponent_item_to_banish, choose_buff_target, choose_rapunzel_discard,
    choose_exert_target, choose_heal_target, choose_look_and_take,
    choose_opponent_discard, choose_play_from_discard,
)


class HeuristicBot:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    def choose(self, state: GameState) -> Action:
        actions = legal_actions(state)
        if not actions:
            return {"kind": "end_turn"}

        scored = [(self._score(state, a), a) for a in actions]
        best_score = max(s for s, _ in scored)
        best = [a for s, a in scored if s == best_score]
        return self.rng.choice(best)

    def choose_target(self, state: GameState, kind: str, options: list):
        """Resolve common target choices with a simple material-aware policy."""
        if kind.startswith("generic_optional:"):
            _, owner_text, effect = kind.split(":")
            owner = int(owner_text)
            if effect.startswith("draw"):
                return bool(state.players[owner].deck)
            return True

        if kind == "discard_choice":
            return choose_discard(state, options)

        if kind == "discard_opponent_action":
            return choose_opponent_discard(state, options)

        if kind == "look_and_take":
            return choose_look_and_take(state, options)

        if kind.startswith("play_from_discard:"):
            return choose_play_from_discard(state, options)

        if kind == "chernabog_target":
            return choose_banish_target(state, options)

        if kind.startswith("chernabog_may_play:"):
            affected_player = int(kind.split(":", 1)[1])
            player = state.players[affected_player]
            return any(state.card_of(iid).type == "Character" for iid in player.discard)

        if kind.startswith("may_discard_to_bounce:"):
            owner = int(kind.split(":", 1)[1])
            return bool(options and state.characters_in_play(state.opponent_idx(owner)))

        if kind.startswith("bounce_target:"):
            owner = int(kind.split(":", 1)[1])
            return choose_bounce_target(state, owner, options)

        if kind.startswith("aladdin_may_pay:"):
            owner = int(kind.split(":", 1)[1])
            return any(state.instances[iid].owner != owner for pidx in (0, 1) for iid in state.players[pidx].play if state.card_of(iid).type == "Item")

        if kind.startswith("aladdin_banish_item:"):
            owner = int(kind.split(":", 1)[1])
            return choose_opponent_item_to_banish(state, owner, options)

        if kind.startswith("angela_challenger_resist_target:"):
            owner = int(kind.split(":", 1)[1])
            return choose_buff_target(state, owner, options)

        if kind.startswith("rapunzel_discard:"):
            owner = int(kind.split(":", 1)[1])
            return choose_rapunzel_discard(state, owner, options)

        if kind == "enter_exerted":
            return False

        if kind.startswith("effect_damage_target:"):
            _, owner_text, amount_text = kind.split(":")
            return choose_damage_target(state, int(owner_text), int(amount_text), options)

        if kind.startswith("effect_exert_target:"):
            owner = int(kind.split(":", 1)[1])
            return choose_exert_target(state, owner, options)

        if kind.startswith("effect_banish_target:"):
            return choose_banish_target(state, options)

        if kind.startswith("effect_heal_target:"):
            owner = int(kind.split(":", 1)[1])
            return choose_heal_target(state, owner, options)

        if kind == "support_target":
            real = [option for option in options if option is not None]
            if not real:
                return None
            return max(real, key=lambda iid: state.card_of(iid).strength or 0)

        if kind.startswith("action_damage_target:"):
            _, owner_text, amount_text = kind.split(":")
            owner = int(owner_text)
            amount = int(amount_text)
            opponent = state.opponent_idx(owner)

            def damage_target_score(iid):
                card = state.card_of(iid)
                inst = state.instances[iid]
                remaining = max((card.willpower or 0) - inst.damage, 0)
                actual_damage = _damage_after_resist(state, iid, amount)
                enemy = inst.owner == opponent
                lethal = actual_damage >= remaining
                overkill = max(actual_damage - remaining, 0)
                return (
                    (1000 if enemy else -1000)
                    + (100 if lethal else 0)
                    + 4 * (card.cost or 0)
                    + 2 * (card.lore or 0)
                    + (card.strength or 0)
                    + 2 * actual_damage
                    - overkill
                )

            return max(options, key=damage_target_score)

        if kind.startswith("action_exert_target:"):
            owner = int(kind.split(":", 1)[1])
            opponent = state.opponent_idx(owner)

            def exert_target_score(iid):
                card = state.card_of(iid)
                inst = state.instances[iid]
                return (
                    (1000 if inst.owner == opponent else -1000)
                    + (0 if inst.exerted else 100)
                    + 4 * (card.lore or 0)
                    + 2 * (card.strength or 0)
                    + (card.cost or 0)
                )

            return max(options, key=exert_target_score)
        return self.rng.choice(options)
    def _score_generic_action(self, state: GameState, iid: int) -> float | None:
        card = state.card_of(iid)
        plan = _generic_action_plan(card)
        if plan is None:
            return None
        owner = state.instances[iid].owner
        opponent = state.opponent_idx(owner)
        score = -1.5 * card.cost

        for effect, value in plan:
            if effect == "draw":
                useful = min(value, max(0, 7 - len(state.players[owner].hand)))
                score += useful * 8
            elif effect == "draw_discard":
                score += 5 if state.players[owner].deck else -12
            elif effect == "lore":
                if state.players[owner].lore + value >= 20:
                    return 10000
                score += 14 * value
            elif effect == "ink_drops":
                score += 6 * value
            elif effect == "next_character_discount":
                score += 5 * value
            elif effect == "bottom_cheap_opposing":
                count = sum(
                    (state.card_of(target).strength or 0) <= value
                    for target in state.characters_in_play(opponent)
                )
                score += 18 * count
            elif effect == "put_top_in_inkwell":
                score += 10
            elif effect == "play_character_from_discard":
                available = [
                    cid for cid in state.players[owner].discard
                    if state.card_of(cid).type == "Character"
                ]
                score += 28 if available else -100
            elif effect == "put_damage_each_opposing":
                enemies = state.characters_in_play(opponent)
                score += sum(
                    10 + (20 if state.instances[target].damage + value >= (state.card_of(target).willpower or 0) else 0)
                    for target in enemies
                )
            elif effect == "damage":
                amount, opposing_only, damaged_only = value
                targets = state.characters_in_play(opponent) if opposing_only else (
                    state.characters_in_play(0) + state.characters_in_play(1)
                )
                if damaged_only:
                    targets = [target for target in targets if state.instances[target].damage > 0]
                enemy_targets = [target for target in targets if state.instances[target].owner == opponent]
                if not enemy_targets:
                    score -= 100
                    continue

                def target_value(target):
                    target_card = state.card_of(target)
                    target_inst = state.instances[target]
                    actual_damage = _damage_after_resist(state, target, amount)
                    remaining = max((target_card.willpower or 0) - target_inst.damage, 0)
                    if actual_damage >= remaining:
                        return 60 + 3 * (target_card.cost or 0) + 2 * (target_card.lore or 0)
                    return 8 * actual_damage + 2 * (target_card.lore or 0) + (target_card.strength or 0)

                score += max(target_value(target) for target in enemy_targets)
            elif effect == "exert":
                opposing_only = value
                targets = state.characters_in_play(opponent) if opposing_only else (
                    state.characters_in_play(0) + state.characters_in_play(1)
                )
                useful_targets = [
                    target for target in targets
                    if state.instances[target].owner == opponent and not state.instances[target].exerted
                ]
                if not useful_targets:
                    score -= 100
                    continue
                score += max(
                    18 + 4 * (state.card_of(target).lore or 0)
                    + 2 * (state.card_of(target).strength or 0)
                    for target in useful_targets
                )
        return score
    def _score(self, state: GameState, action: Action) -> float:
        kind = action["kind"]

        if kind == "challenge":
            attacker_iid = action["attacker_iid"]
            defender_iid = action["defender_iid"]
            attacker = state.card_of(attacker_iid)
            defender = state.card_of(defender_iid)
            damage_to_defender = _damage_after_resist(
                state, defender_iid, _effective_strength(state, attacker_iid, is_challenger=True)
            )
            remaining_defender = max(
                (defender.willpower or 0) - state.instances[defender_iid].damage, 0
            )
            banishes_defender = damage_to_defender >= remaining_defender
            if action["target_type"] == "location":
                if banishes_defender:
                    return 90 + (defender.cost or 0) + (defender.lore or 0)
                return 5 + damage_to_defender

            damage_to_attacker = _damage_after_resist(
                state, attacker_iid, _effective_strength(state, defender_iid, is_challenger=False)
            )
            remaining_attacker = max(
                (attacker.willpower or 0) - state.instances[attacker_iid].damage, 0
            )
            loses_attacker = damage_to_attacker >= remaining_attacker
            if banishes_defender and not loses_attacker:
                return 100 + (defender.cost or 0)
            if banishes_defender and loses_attacker:
                return 40 + (defender.cost or 0) - (attacker.cost or 0)
            if loses_attacker:
                return -100 - (attacker.cost or 0)
            return -50

        if kind == "quest":
            card = state.card_of(action["iid"])
            return 60 + 10 * (card.lore or 0)

        if kind in ("play_character", "play_item", "play_location"):
            card = state.card_of(action["hand_iid"])
            return 20 + (card.cost or 0)

        if kind == "play_action":
            card = state.card_of(action["hand_iid"])
            if card.full_name in ACTION_EFFECT_HANDLERS or card.name in ACTION_EFFECT_HANDLERS:
                return 15 + (card.cost or 0)
            generic_score = self._score_generic_action(state, action["hand_iid"])
            return generic_score if generic_score is not None else -20

        if kind == "ink":
            card = state.card_of(action["hand_iid"])
            return 10 - (card.cost or 0) * 0.1  # prefer inking cheap/low-impact cards

        if kind == "move":
            return 5

        if kind == "end_turn":
            return 0

        return 0
