"""A real search-based bot: depth-limited minimax with alpha-beta pruning
over the action-level game tree (not the coarser "whole turn" level — each
ply here is one atomic action from `legal_actions`).

Two correctness/design points worth knowing before tuning this further:

- Root actions are evaluated across sampled hidden positions when the game
  uses hidden information. The default four samples keep the acting player's
  hand known and randomize hidden deck/hand positions. `max_nodes` applies to
  each root action in each sample, while `max_total_nodes` is a strict
  per-decision ceiling.
- `max_depth` is counted in actions, not turns, and a typical turn here
  runs ~5 actions (ink, several plays, quests, challenges). The default
  depth aims to cross a turn boundary.

Move ordering prioritizes likely actions. Alpha-beta pruning applies within
each sampled game tree. Root action values are averaged across samples so a
cutoff from one hidden position cannot bias the average.
Bounded in other ways too:
- Sub-decisions made *inside* an action's resolution (e.g. Support's
  "chosen character", or any card_effects.py handler calling
  `state.choose(...)`) are NOT searched separately — they're resolved by
  this bot's own `choose_target` heuristic during simulated rollouts, the
  same way for both players. Actually searching those too would multiply
  branching factor further for comparatively little benefit.
- The static evaluation (`_evaluate`) is a hand-written material/lore
  heuristic with hand-picked weights — not tuned, not learned.

Uses `GameState.clone()` (not `copy.deepcopy`) to simulate moves cheaply
without duplicating the shared card database on every node.

Honest status (see README "Step 2" section for the numbers and how they
were produced): this reliably beats RandomBot, but validation against
HeuristicBot was done at a small sample size (games are slow — real
search, not a lookup) and the results were mixed/close rather than a
clear win. That's a genuine, currently-open question, not a solved one —
see the README before assuming this bot is strictly better in practice.
"""
from __future__ import annotations

import math
import random

from lorcana.engine import (
    legal_actions, apply_action, Action, _effective_strength, _damage_after_resist,
)
from lorcana.state import GameState
from bots.targeting import (
    choose_banish_target, choose_bounce_target, choose_damage_target, choose_discard,
    choose_opponent_item_to_banish, choose_buff_target, choose_rapunzel_discard,
    choose_exert_target, choose_heal_target, choose_look_and_take,
    choose_opponent_discard, choose_play_from_discard,
)


def describe_action(state: GameState, action: Action) -> str:
    """Human-readable rendering of an action, for trace logs."""
    kind = action["kind"]
    if kind == "ink":
        return f"ink {state.card_of(action['hand_iid']).full_name}"
    if kind == "play_character":
        card = state.card_of(action["hand_iid"])
        if action.get("pay") == "shift":
            target_iids = action.get("target_iids") or [action["target_iid"]]
            targets = " and ".join(state.card_of(iid).full_name for iid in target_iids)
            return f"play {card.full_name} (Shift onto {targets})"
        tag = " (enter exerted)" if action.get("enter_exerted") else ""
        return f"play {card.full_name}{tag}"
    if kind in ("play_item", "play_location"):
        return f"play {state.card_of(action['hand_iid']).full_name}"
    if kind == "play_action":
        card = state.card_of(action["hand_iid"])
        pay = action.get("pay")
        tag = " (sung)" if pay in ("sing", "sing_together") else ""
        return f"play {card.full_name}{tag}"
    if kind == "quest":
        return f"quest with {state.card_of(action['iid']).full_name}"
    if kind == "challenge":
        return f"challenge {state.card_of(action['defender_iid']).full_name} with {state.card_of(action['attacker_iid']).full_name}"
    if kind == "move":
        return f"move {state.card_of(action['char_iid']).full_name} to {state.card_of(action['location_iid']).full_name}"
    if kind == "activate_ability":
        return f"activate {action['ability_name']} on {state.card_of(action['iid']).full_name}"
    if kind == "boost":
        return f"Boost with {state.card_of(action['iid']).full_name}"
    if kind == "end_turn":
        return "end turn"
    return str(action)


class SearchBot:
    def __init__(self, rng: random.Random | None = None, max_depth: int = 8, max_nodes: int = 600,
                 max_total_nodes: int = 6000, verbose: bool = False, evaluate_fn=None,
                 information_samples: int = 4):
        self.rng = rng or random.Random()
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_total_nodes = max_total_nodes
        self.information_samples = max(1, information_samples)
        # Pluggable leaf evaluation: f(state, me) -> float. Defaults to the
        # hand-written heuristic below. Pass e.g. a trained value network's
        # forward pass (see bots/neural_bot.py) to use that instead — the
        # search algorithm (minimax/alpha-beta/iterative deepening/move
        # ordering) is entirely unchanged either way; only the leaf judgment
        # differs.
        self._evaluate_fn = evaluate_fn
        self._nodes_visited = 0
        self._total_nodes_visited = 0
        self._sim_decision_maker = lambda st, pidx, kind, options: self.choose_target(st, kind, options)
        # "Keep the hood open": when True, every choose() call appends a full
        # trace record to self.trace_log — every candidate action considered
        # at the deepest completed iteration, its minimax value, node counts,
        # and which one was picked. Off by default since it costs a bit of
        # string-building overhead per decision; turn on to actually inspect
        # what the bot is doing, not just its win/loss record.
        self.verbose = verbose
        self.trace_log: list[dict] = []

    def _sample_hidden_state(self, state: GameState, player_idx: int) -> GameState:
        """Sample hidden card positions while preserving each owner's unseen pool.

        Own hand cards remain known. When both visibility flags are closed,
        the opponent's hand and deck are shuffled together so the search does
        not get their actual hand or draw order. The simulator still supplies
        the opponent's remaining card pool as the sampling prior.
        """
        sample = state.clone()
        for pidx, player in enumerate(sample.players):
            hidden_slots = []
            if not sample.config.open_decks:
                hidden_slots.extend(player.deck)
                if pidx != player_idx and not sample.config.open_hands:
                    hidden_slots.extend(player.hand)
            elif pidx != player_idx and not sample.config.open_hands:
                hidden_slots.extend(player.hand)

            hidden_card_ids = [sample.instances[iid].card_id for iid in hidden_slots]
            self.rng.shuffle(hidden_card_ids)
            for iid, card_id in zip(hidden_slots, hidden_card_ids):
                sample.instances[iid].card_id = card_id
        return sample

    def choose(self, state: GameState) -> Action:
        actions = legal_actions(state)
        if not actions:
            return {"kind": "end_turn"}
        if len(actions) == 1:
            return actions[0]

        me = state.active_idx
        ordered = self._order_actions(state, actions)
        best_action = ordered[0]
        depth_reached = 0
        last_completed_scores: list[tuple[Action, float]] = []
        self._total_nodes_visited = 0
        hidden = not state.config.open_hands or not state.config.open_decks
        sample_count = self.information_samples if hidden else 1
        root_states = [self._sample_hidden_state(state, me) for _ in range(sample_count)]
        for root in root_states:
            root.decision_maker = self._sim_decision_maker

        # Each depth is accepted only after every root action has been
        # evaluated across every hidden-state sample.
        for depth in range(1, self.max_depth + 1):
            totals = [0.0] * len(ordered)
            completed = True
            for root in root_states:
                for action_index, action in enumerate(ordered):
                    if self._total_nodes_visited >= self.max_total_nodes:
                        completed = False
                        break
                    self._nodes_visited = 0
                    child = root.clone()
                    apply_action(child, action, validate=False)
                    # Root actions are evaluated separately across samples;
                    # averaging exact root values avoids information-dependent
                    # alpha-beta cutoffs biasing the sample mean.
                    value = self._minimax(child, depth - 1, -math.inf, math.inf, me)
                    totals[action_index] += value
                if not completed:
                    break
            if not completed:
                break

            scores_this_depth = [
                (action, total / sample_count)
                for action, total in zip(ordered, totals)
            ]
            best_val = max(value for _, value in scores_this_depth)
            best_actions = [action for action, value in scores_this_depth if value == best_val]
            best_action = self.rng.choice(best_actions)
            depth_reached = depth
            last_completed_scores = scores_this_depth
            ordered = [best_action] + [action for action in ordered if action is not best_action]

        if self.verbose:
            self.trace_log.append({
                "turn": state.turn_number,
                "player": me,
                "depth_reached": depth_reached,
                "total_nodes": self._total_nodes_visited,
                "information_samples": sample_count,
                "candidates": sorted(
                    [{"action": describe_action(state, action), "value": value}
                     for action, value in last_completed_scores],
                    key=lambda candidate: candidate["value"], reverse=True,
                ),
                "chosen": describe_action(state, best_action),
            })

        return best_action

    def _order_actions(self, state: GameState, actions: list[Action]) -> list[Action]:
        """Cheap static ordering (no simulation) so alpha-beta sees the
        most-promising moves first and prunes far more of the tree for the
        same node budget. Reuses the same rough shape as HeuristicBot's
        scoring — good enough to order by, not meant as the real
        evaluation."""
        def quick_score(a: Action) -> float:
            kind = a["kind"]
            if kind == "challenge":
                attacker_iid = a["attacker_iid"]
                defender_iid = a["defender_iid"]
                defender = state.card_of(defender_iid)
                damage = _damage_after_resist(
                    state, defender_iid, _effective_strength(state, attacker_iid, is_challenger=True)
                )
                if a["target_type"] == "location":
                    remaining = max((defender.willpower or 0) - state.instances[defender_iid].damage, 0)
                    return 90 + (defender.cost or 0) if damage >= remaining else 5 + damage
                remaining = max((defender.willpower or 0) - state.instances[defender_iid].damage, 0)
                return 100 + (defender.cost or 0) if damage >= remaining else -10
            if kind == "quest":
                return 60 + 10 * (state.card_of(a["iid"]).lore or 0)
            if kind in ("play_character", "play_item", "play_location", "play_action"):
                return 20 + (state.card_of(a["hand_iid"]).cost or 0)
            if kind in ("activate_ability", "boost"):
                return 25
            if kind == "ink":
                return 10
            if kind == "move":
                return 5
            return 0  # end_turn and anything else explored last

        return sorted(actions, key=quick_score, reverse=True)

    def _minimax(self, state: GameState, depth: int, alpha: float, beta: float, me: int) -> float:
        self._nodes_visited += 1
        self._total_nodes_visited += 1
        if state.winner is not None:
            return 1_000_000.0 if state.winner == me else -1_000_000.0
        if (
            depth <= 0
            or self._nodes_visited >= self.max_nodes
            or self._total_nodes_visited >= self.max_total_nodes
        ):
            return self._evaluate(state, me)

        actions = legal_actions(state)
        if not actions:
            child = state.clone()
            apply_action(child, {"kind": "end_turn"}, validate=False)
            return self._minimax(child, depth - 1, alpha, beta, me)

        actions = self._order_actions(state, actions)
        maximizing = state.active_idx == me
        value = -math.inf if maximizing else math.inf
        for a in actions:
            child = state.clone()
            apply_action(child, a, validate=False)
            child_val = self._minimax(child, depth - 1, alpha, beta, me)
            if maximizing:
                value = max(value, child_val)
                alpha = max(alpha, value)
            else:
                value = min(value, child_val)
                beta = min(beta, value)
            if alpha >= beta:
                break  # prune
            if (
                self._nodes_visited >= self.max_nodes
                or self._total_nodes_visited >= self.max_total_nodes
            ):
                break
        return value

    def _evaluate(self, state: GameState, me: int) -> float:
        """Static position evaluation from `me`'s perspective. Weights are
        hand-picked, not tuned — a reasonable starting point, not a claim
        of optimality. Lore dominates (it's literally how you win); board
        stats and card advantage are the tie-breakers that matter for the
        moves the lore term alone can't distinguish between."""
        if state.winner is not None:
            return 1_000_000.0 if state.winner == me else -1_000_000.0

        if self._evaluate_fn is not None:
            return self._evaluate_fn(state, me)

        opp = state.opponent_idx(me)
        lore_gap = state.players[me].lore - state.players[opp].lore
        score = 20.0 * lore_gap
        # Winning is valuable, and getting close to 20 matters more than
        # linear material differences.
        for pidx, sign in ((me, 1.0), (opp, -1.0)):
            score += sign * 0.15 * state.players[pidx].lore ** 2
            for iid in state.characters_in_play(pidx):
                card = state.card_of(iid)
                inst = state.instances[iid]
                remaining_willpower = max((card.willpower or 0) - inst.damage, 0)
                score += sign * (
                    0.5 * (card.strength or 0)
                    + 0.5 * remaining_willpower
                    + 0.3 * (card.lore or 0)
                    + (0.35 if not inst.exerted else 0.0)
                    + (0.25 if state.is_dry(iid) else 0.0)
                )
            for iid in state.locations_in_play(pidx):
                card = state.card_of(iid)
                inst = state.instances[iid]
                remaining_willpower = max((card.willpower or 0) - inst.damage, 0)
                score += sign * (
                    0.4 * remaining_willpower
                    + 0.5 * (card.lore or 0)
                    + 0.2 * (card.move_cost or 0)
                )
            score += sign * 0.75 * len(state.players[pidx].hand)
            # Ink development: without this, the evaluator can't tell inking
            # apart from passing (they looked identical at the leaf), so the
            # search would tie-break between them essentially at random —
            # confirmed via trace_log, see README "Step 2" notes. More ink
            # means more of your hand becomes playable in future turns.
            score += sign * 1.5 * len(state.players[pidx].inkwell)
            score += sign * 1.5 * state.players[pidx].ink_drops

        return score

    def choose_target(self, state: GameState, kind: str, options: list):
        """Choose useful targets for Support and generic action effects.

        These sub-decisions are not searched as separate plies, so use the
        same deterministic tactical preference in live play and rollouts.
        """
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