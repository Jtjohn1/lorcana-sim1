"""A real search-based bot: depth-limited minimax with alpha-beta pruning
over the action-level game tree (not the coarser "whole turn" level — each
ply here is one atomic action from `legal_actions`).

Two correctness/design points worth knowing before tuning this further:

- Each top-level candidate action gets its OWN `max_nodes` budget (reset
  per candidate in `choose()`). An earlier version shared one budget
  across all candidates, so whichever action got evaluated first consumed
  most of the budget and the rest got almost no search at all — an unfair
  comparison, not a real minimax. Keep it this way.
- `max_depth` is counted in *actions*, not turns, and a typical turn here
  runs ~5 actions (ink, several plays, quests, challenges). A depth that
  doesn't comfortably exceed that never sees the opponent's reply at
  all — it's just a shallow same-turn lookahead. The default below is
  picked to reliably cross at least one turn boundary.

Static move ordering (`_order_actions`, reusing HeuristicBot's rough
scoring shape) is applied before every expansion, top-level and internal,
so alpha-beta sees likely-good moves first and prunes far more of the
tree for the same node budget — this matters a lot given how small
`max_nodes` has to be to keep games running in reasonable time.

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

from lorcana.engine import legal_actions, apply_action, Action
from lorcana.state import GameState


def describe_action(state: GameState, action: Action) -> str:
    """Human-readable rendering of an action, for trace logs."""
    kind = action["kind"]
    if kind == "ink":
        return f"ink {state.card_of(action['hand_iid']).full_name}"
    if kind == "play_character":
        card = state.card_of(action["hand_iid"])
        if action.get("pay") == "shift":
            target = state.card_of(action["target_iid"])
            return f"play {card.full_name} (Shift onto {target.full_name})"
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
                 max_total_nodes: int = 6000, verbose: bool = False, evaluate_fn=None):
        self.rng = rng or random.Random()
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.max_total_nodes = max_total_nodes
        # Pluggable leaf evaluation: f(state, me) -> float. Defaults to the
        # hand-written heuristic below. Pass e.g. a trained value network's
        # forward pass (see bots/neural_bot.py) to use that instead — the
        # search algorithm (minimax/alpha-beta/iterative deepening/move
        # ordering) is entirely unchanged either way; only the leaf judgment
        # differs.
        self._evaluate_fn = evaluate_fn
        self._nodes_visited = 0
        self._sim_decision_maker = lambda st, pidx, kind, options: self.choose_target(st, kind, options)
        # "Keep the hood open": when True, every choose() call appends a full
        # trace record to self.trace_log — every candidate action considered
        # at the deepest completed iteration, its minimax value, node counts,
        # and which one was picked. Off by default since it costs a bit of
        # string-building overhead per decision; turn on to actually inspect
        # what the bot is doing, not just its win/loss record.
        self.verbose = verbose
        self.trace_log: list[dict] = []

    def choose(self, state: GameState) -> Action:
        actions = legal_actions(state)
        if not actions:
            return {"kind": "end_turn"}
        if len(actions) == 1:
            return actions[0]

        me = state.active_idx
        ordered = self._order_actions(state, actions)
        best_action = ordered[0]
        total_nodes = 0
        depth_reached = 0
        last_completed_scores: list[tuple[Action, float]] = []

        # Iterative deepening: search depth 1, then 2, then 3, ... Each
        # iteration reorders candidates with the previous iteration's best
        # move first, so alpha-beta at the next (more expensive) depth sees
        # the most-promising move first and prunes far more of the tree.
        # Also gives an anytime cutoff: if the total node budget runs out
        # mid-depth, we still have a valid answer from the last completed
        # iteration rather than nothing.
        for depth in range(1, self.max_depth + 1):
            best_val = -math.inf
            best_actions: list[Action] = []
            scores_this_depth: list[tuple[Action, float]] = []
            alpha, beta = -math.inf, math.inf

            for a in ordered:
                self._nodes_visited = 0  # each candidate gets its OWN budget — see class docstring
                child = state.clone()
                child.decision_maker = self._sim_decision_maker
                apply_action(child, a)
                val = self._minimax(child, depth - 1, alpha, beta, me)
                total_nodes += self._nodes_visited
                scores_this_depth.append((a, val))
                if val > best_val:
                    best_val = val
                    best_actions = [a]
                elif val == best_val:
                    best_actions.append(a)
                alpha = max(alpha, best_val)

            best_action = self.rng.choice(best_actions)
            depth_reached = depth
            last_completed_scores = scores_this_depth
            ordered = [best_action] + [a for a in ordered if a is not best_action]
            if total_nodes > self.max_total_nodes:
                break  # anytime cutoff — keep the last fully-completed depth's answer

        if self.verbose:
            self.trace_log.append({
                "turn": state.turn_number,
                "player": me,
                "depth_reached": depth_reached,
                "total_nodes": total_nodes,
                "candidates": sorted(
                    [{"action": describe_action(state, a), "value": v} for a, v in last_completed_scores],
                    key=lambda c: c["value"], reverse=True,
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
                attacker = state.card_of(a["attacker_iid"])
                defender = state.card_of(a["defender_iid"])
                if (attacker.strength or 0) >= (defender.willpower or 0):
                    return 100 + (defender.cost or 0)
                return -10
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
        if state.winner is not None:
            return 1_000_000.0 if state.winner == me else -1_000_000.0
        if depth <= 0 or self._nodes_visited > self.max_nodes:
            return self._evaluate(state, me)

        actions = legal_actions(state)
        if not actions:
            child = state.clone()
            apply_action(child, {"kind": "end_turn"})
            return self._minimax(child, depth - 1, alpha, beta, me)

        actions = self._order_actions(state, actions)
        maximizing = state.active_idx == me
        value = -math.inf if maximizing else math.inf
        for a in actions:
            child = state.clone()
            apply_action(child, a)
            child_val = self._minimax(child, depth - 1, alpha, beta, me)
            if maximizing:
                value = max(value, child_val)
                alpha = max(alpha, value)
            else:
                value = min(value, child_val)
                beta = min(beta, value)
            if alpha >= beta:
                break  # prune
            if self._nodes_visited > self.max_nodes:
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
        score = 20.0 * (state.players[me].lore - state.players[opp].lore)

        for pidx, sign in ((me, 1.0), (opp, -1.0)):
            for iid in state.characters_in_play(pidx):
                card = state.card_of(iid)
                inst = state.instances[iid]
                remaining_willpower = max((card.willpower or 0) - inst.damage, 0)
                score += sign * (0.5 * (card.strength or 0) + 0.5 * remaining_willpower + 0.3 * (card.lore or 0))
            score += sign * 0.75 * len(state.players[pidx].hand)
            # Ink development: without this, the evaluator can't tell inking
            # apart from passing (they looked identical at the leaf), so the
            # search would tie-break between them essentially at random —
            # confirmed via trace_log, see README "Step 2" notes. More ink
            # means more of your hand becomes playable in future turns.
            score += sign * 1.5 * len(state.players[pidx].inkwell)

        return score

    def choose_target(self, state: GameState, kind: str, options: list):
        """Fallback policy for sub-decisions (Support targets, discard
        choices, etc.) — used both during real play and during this bot's
        own simulated rollouts. Simple, not searched. Same "support_target"
        special-case as HeuristicBot; everything else picks uniformly."""
        if kind == "support_target":
            real = [o for o in options if o is not None]
            if not real:
                return None
            return max(real, key=lambda iid: state.card_of(iid).strength or 0)
        return self.rng.choice(options)
