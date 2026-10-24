# Lorcana Self-Play Simulator

A Python engine for Disney Lorcana that can play full legal games
autonomously, aimed at eventually running bot-vs-bot self-play to
evaluate and evolve decks (like a chess engine, but for Lorcana).

Built from the Comprehensive Rules v2.2.0 (effective July 9, 2026) and
LorcanaJSON card data for sets 9–13 (Fabled, Whispers in the Well,
Winterspell, Wilds Unknown, Attack of the Vine!) — 1,294 cards total.

## Status: engine skeleton, not a finished game

Full legal games run start-to-finish (see `self_play.py`), but a large
share of Lorcana's actual complexity is *unique per-card text*, which
isn't scripted yet. See "Known gaps" below before assuming a game
result reflects real strategy.

## Layout

```
data/
  raw/setdata_9.json ... setdata_13.json   # source LorcanaJSON dumps
  build_card_db.py                          # merges raw/ -> cards.json
  cards.json                                # compiled, normalized card DB
lorcana/
  cards.py       # Card / Ability data model + CardDatabase loader
  state.py       # GameState, PlayerState, CardInstance, GameConfig (visibility)
  engine.py      # turn structure, legal_actions(), apply_action(), extension registries
  card_effects.py  # actual scripted card text (see "What's implemented" below)
  decklist.py    # parse "N Card Name" decklist text files into card id lists
  deck_utils.py  # build_random_deck() — legality-only random decks for testing
data/decklists/
  deck1.txt, deck2.txt  # the two 60-card decks provided in-session (see below)
bots/
  random_bot.py     # uniformly random legal move (correctness baseline)
  heuristic_bot.py  # cheap priority rules, no search
self_play.py     # CLI: run N bot-vs-bot games (random decks, or --deck-a/--deck-b files)
```

## Quick start

```bash
python3 self_play.py --games 20 --bot heuristic
python3 self_play.py --games 1 --bot heuristic --verbose   # full move log
# with real decklists instead of random ones:
python3 self_play.py --games 20 --bot heuristic --deck-a data/decklists/deck1.txt --deck-b data/decklists/deck2.txt
```

To add a new set later: drop its `setdata_N.json` into `data/raw/` and
rerun `python3 data/build_card_db.py`. To test a deck you care about,
save it as `N Card Name` per line (blank lines and `//` comments OK) and
pass its path to `--deck-a`/`--deck-b`.

## The open-hands / open-decks config

`lorcana.state.GameConfig` has two flags:

- `open_hands` — if `True`, `GameState.view_for(player)` includes the
  opponent's hand contents, not just its count.
- `open_decks` — if `True`, `view_for()` includes both players' full
  deck order, not just deck size.

**Both default to `True`.** This makes the game perfect-information by
default: no hidden information to model, so a search algorithm (minimax,
MCTS, etc.) can look at the whole game tree directly instead of needing
determinization/sampling over unknown hands and draws.

Important nuance: **the engine and the bundled bots don't currently call
`view_for()` at all** — they operate on the real `GameState` directly,
which already has full access to everything. `view_for()` only matters
once something needs a *restricted* picture (a human opponent, or an
experiment in hidden-information search). Turning `open_hands`/
`open_decks` to `False` today changes what `view_for()` reports; it
doesn't yet change what a bot can see, since no bot consults it. Wiring
the bots through `view_for()` — and giving them a sampling/determinization
strategy for the hidden case — is a real next step, not a config flip.

## What's implemented

- Full turn structure: Start-of-Turn (ready / set / draw) → Main Phase →
  End-of-Turn, matching Comprehensive Rules section 3, including
  `on_start_of_turn` and `on_end_of_turn` broadcast hooks for card text
  tied to those moments.
- Turn actions: ink, play character/item/location/action, quest,
  challenge (character or location), move to a location, activate an
  activated ability, Boost, end turn.
- Deck/hand/play/inkwell/discard/**under** zones (the last for Boost —
  cards tucked under a character), with correct state resets on zone
  change.
- Win/loss conditions: 20+ lore, deck-out at end of turn.
- Game state checks: banishment via damage ≥ willpower (including
  discarding anything Boosted under a banished character), checked
  after every action.
- Keywords implemented mechanically: **Rush, Evasive, Alert, Bodyguard
  (including its "may enter play exerted" choice, generalized to any
  card whose text says that — e.g. Mickey Mouse's Long Journey),
  Challenger, Resist, Reckless** (including its "can't end turn" lock),
  **Support, Boost, Shift** (base, `[Classification] Shift`,
  `Universal Shift`), **Singer, Sing Together** (simplified — see
  gaps). Singing now correctly follows the universal rule (5.4.4.2: ANY
  character with cost ≥ the song's cost can sing it, not just ones with
  the Singer keyword) — this was a real bug in the first version of this
  engine, fixed this session. **Ward** is tracked as a flag but has no
  effect yet since no targeted effects need blocking (Ward doesn't block
  challenges per the rules anyway).
- A **target-selection / sub-decision framework**: `GameState.choose(player_idx,
  kind, options)` — falls back to a random pick if nothing's wired up, or
  calls `state.decision_maker(state, player_idx, kind, options)` if set
  (the self-play driver points this at each bot's `choose_target`
  method).
- **Generic activated abilities** (Comprehensive Rules 4.4): any ability
  with `type: "activated"` and a recognized cost shape (`⟳`, `N ⬡`,
  `Banish this X`) is automatically offered as a legal action once you
  register a handler in `ACTIVATED_ABILITY_HANDLERS` — the engine parses
  and pays the cost generically (see `_parse_activated_costs` in
  `engine.py`).
- **A cost-reduction pool** (`state.next_character_discount`): stacks
  from multiple sources (per-turn statics like Grandmother Willow,
  activated abilities like Hamm/Lantern/Pluto, triggered ones like
  Aurora), consumed by the next character played for ink.
- **Two temporary-modifier ledgers**: `state.temp_strength_bonus`
  ("this turn," cleared at your End-of-Turn) and
  `state.delayed_strength_mods` ("until the start of your next turn,"
  survives the opponent's whole turn — e.g. Mickey Mouse's Secret Path).
- **Dynamic Lore/damage-source hooks**: `STATIC_LORE_MODIFIERS` (e.g.
  The Queen's Jealous Heart) and `STATIC_DAMAGE_SOURCE_OVERRIDE` (e.g.
  Dale's Spike Suit — challenge damage uses Willpower instead of
  Strength for that controller's characters).
- **`STATIC_READY_BLOCKERS`**: a card can prevent its own instance from
  readying based on a live condition (e.g. Demona's Stone by Day).
- **`lorcana/card_effects.py`** scripts every unique card (beyond generic
  keywords) across the two decklists provided this session — 21 cards'
  worth of triggered/static/activated abilities, action-card effects,
  and one Boost interaction. This is the reference set to copy the
  pattern from for any other card.

## Decks validated this session

`data/decklists/deck1.txt` and `deck2.txt` — two 60-card decks provided
directly, now fully scripted (every card either uses a generic keyword
or has a handler in `card_effects.py`). 400+ self-play games run against
each other (random-vs-random and heuristic-vs-heuristic) with zero
crashes. One thing worth flagging honestly rather than burying: with
`HeuristicBot` on both sides, deck2 won roughly 92% of games (184/200)
versus a much more even ~53%/47% split under `RandomBot`. `HeuristicBot`
is a simple priority-scoring bot with no lookahead, and its scoring
doesn't specifically value conditional removal spells (deck1 has several:
Gaston, World's Greatest Criminal Mind, Can't Hold It Back Anymore) the
way it values straightforward stat-check plays (which deck2 leans on more).
That's a plausible bot-weakness explanation rather than a confirmed power
gap — it's exactly the kind of thing a real search-based bot (roadmap
step 2 below) would resolve, since it wouldn't systematically undervalue
one deck's cards over the other's.

## Interpretation calls made while scripting (revisit if wrong)

A few cards' extracted text was ambiguous enough that a judgment call was
needed. Each is commented at its handler in `card_effects.py`, listed
here too:
- **Hades - Looking for a Deal** ("WHAT D'YA SAY?"): the extracted text
  ("draw 2 cards unless that character's player puts that card on the
  bottom of their deck") doesn't obviously parse. Implemented as: the
  chosen opposing character's controller decides between bottoming that
  *character* (denying the draw) or letting Hades' controller draw 2.
  Fits the "making a deal" flavor; fix if you have the official ruling.
- **Aurora - Holding Court** ("Royal Welcome"): text scopes the discount
  to "the next Princess or Queen character"; implemented as an unscoped
  discount (any next character) via the shared discount pool, for
  simplicity. Minor over-application only if a non-Princess/Queen
  character is played first while the discount is still banked.
- **Mrs. Incredible - Super Stretchy** ("Flexible Thinking"): only the
  "+1 Lore this turn" branch of its "choose one" is implemented; the
  Evasive-grant branch needs a temporary-keyword-grant mechanism no
  other card here needs yet, so it's not offered as a choice at all
  (this handler always takes the Lore branch).
- **Dumbo's "Making History"**: the static ability that grants Dumbo's
  own activated ability to other Evasive characters isn't wired (would
  need per-instance ability grants) — Dumbo's own activated ability
  (Breaking Records) works fine.
- **Cheshire Cat's "It's Loads of Fun"**: moves a flat 2 damage rather
  than offering a 0/1/2 "up to 2" amount choice.

## Step 2: a real search-based bot

`bots/search_bot.py` implements `SearchBot`: depth-limited minimax with
alpha-beta pruning over the action-level game tree (each ply is one
atomic action from `legal_actions`, not a whole turn). It uses
`GameState.clone()` — a hand-written cheap copy that shares the card
database by reference instead of deep-copying it — rather than
`copy.deepcopy`, since the latter would duplicate the ~1,300-card
database on every simulated node.

Getting this working correctly surfaced two real bugs worth knowing
about if you tune this further, not just "gaps":

1. **Shared node budget across sibling actions.** The first version gave
   the whole `choose()` call one node budget, so whichever candidate
   action got evaluated first consumed most of it and the rest got
   almost no search — an unfair comparison dressed up as minimax. Fixed
   by resetting the budget per top-level candidate.
2. **Depth measured in actions, not turns.** A typical turn here runs
   ~5 actions (ink, several plays, quests, challenges). At `max_depth=4`,
   the search never once reached `end_turn` — it was evaluating "the
   best 4 things I can do this turn" without ever seeing the opponent's
   reply, which isn't really search at all. Confirmed by direct
   measurement (see this session's transcript), then fixed by raising
   `max_depth` to reliably cross a turn boundary.

On top of those fixes, static move ordering (`_order_actions`, reusing
`HeuristicBot`'s rough scoring shape to sort candidates before
expansion, at every level of the tree — not just the top) makes
alpha-beta prune far more of the tree for the same node budget, which
matters a lot given how small that budget has to be to keep games
running in reasonable time. **Iterative deepening** was added on top of
that: `choose()` searches depth 1, then 2, then 3… up to `max_depth`,
each time putting the previous depth's best move first before the next
(more expensive) depth — this is the standard way to get much stronger
move ordering than any hand-written heuristic can, and it also gives an
anytime cutoff (`max_total_nodes`): if the budget runs out mid-depth, the
last *fully completed* depth's answer is still used rather than nothing.

### Honest results

- **SearchBot vs RandomBot**: 14/15 games (93%). Solidly, unsurprisingly
  dominant — any real lookahead beats no lookahead.
- **SearchBot vs HeuristicBot**: genuinely inconclusive, and I want to be
  upfront about why rather than paper over it. Across several small
  batches this session (6–10 games each, at different stages of fixing
  bugs and adding move ordering / iterative deepening), results have
  bounced between clearly losing, roughly even, and clearly losing
  again — with no consistent trend. Real minimax over this game's action
  tree is expensive in pure Python (roughly half a second to several
  seconds per decision), which means every sample gathered here was
  small enough that noise plausibly explains the swings. **This is not
  a validated result either way** — it would take a much larger sample
  (dozens of games, meaning tens of minutes of wall-clock compute) to
  say with confidence whether SearchBot currently beats HeuristicBot,
  and that run didn't fit in this session's available time.
- Default settings: `max_depth=8`, `max_nodes=600` (per candidate
  action per depth), `max_total_nodes=6000` (anytime cutoff across the
  whole decision). All three are constructor args with no principled
  claim to being optimal — they're what fit this session's time budget,
  not the result of tuning.

### What would most likely make it stronger next

- **A better/tuned evaluation function** — `_evaluate`'s weights are
  hand-picked, not tuned against anything. Given move ordering and
  iterative deepening are now in place, this is probably the highest-
  leverage remaining lever: the search is only as good as the position
  judgment it optimizes toward.
- **Performance work**, specifically to make large-sample validation
  tractable at all: the per-node cost (clone + apply_action + legal_actions)
  is the real bottleneck, not the search algorithm's structure at this
  point. Profiling `GameState.clone()` and `legal_actions()` for
  avoidable overhead would let far more games run in the same wall-clock
  budget — which is a prerequisite for trusting any future "SearchBot
  wins X%" number, this session's included.
- **Caching/transposition tables** — currently every node is
  re-evaluated from scratch even if the same position is reached by a
  different action order (common when action order within a turn
  doesn't matter, e.g. questing with character A then B vs. B then A).
- Once large-sample validation is actually feasible, revisit the
  deck1-vs-deck2 win-rate question from the previous session with a bot
  whose verdicts can be trusted.

### New decklists (not yet scripted)

`data/decklists/deck3.txt` through `deck11.txt` — nine more 60-card
decks provided in-session, all fully present in the sets 9–13 database
(no missing cards). None of their unique abilities are scripted in
`card_effects.py` yet (only deck1/deck2's 21 cards are) — playing these
decks today means every card not covered by a generic keyword behaves
as a no-op (still legal to play, costs what it costs, but its printed
text does nothing). Scripting these is the natural next "step 1" pass,
independent of the search-bot work above.

## A learned (Transformer) evaluation function

`lorcana/nn/` implements a small Transformer encoder, following Vaswani
et al. 2017 ("Attention Is All You Need" — see the paper in this
project's history), trained to predict a position's value, as a
pluggable replacement for `SearchBot`'s hand-written `_evaluate`.

**Why not PyTorch**: `pip install torch` pulls a CUDA-enabled wheel that
hard-requires CUDA shared libraries even for CPU-only tensor ops; the
actual CPU-only build is hosted on `download.pytorch.org`, which isn't
in this sandbox's network allowlist. Rather than fight that, `lorcana/nn/`
implements a minimal reverse-mode autodiff engine over NumPy
(`autodiff.py`) and builds the Transformer encoder on top of it directly
— arguably a closer reading of "use this paper as a base" than importing
a library would have been anyway.

**Layout**:
```
lorcana/nn/
  autodiff.py        # minimal NumPy autodiff Tensor (matmul, softmax, etc.)
  test_autodiff.py    # numerical gradient checks — run this before trusting any change here
  transformer.py      # Linear, LayerNorm, MultiHeadSelfAttention, EncoderLayer, ValueNetwork
  state_encoding.py   # GameState -> token features
  generate_data.py    # self-play data generation (real engine, real decks)
  train.py            # Adam optimizer + training loop; saves lorcana/nn/weights.pkl
bots/neural_bot.py    # NeuralBot (standalone) and make_neural_evaluate_fn (for SearchBot)
```

**Correctness, verified before trusting any of this for training**: every
primitive op in `autodiff.py`, plus the full `ValueNetwork` end-to-end
(2 encoder layers, multi-head attention, the works), passes numerical
gradient checking (`test_autodiff.py` and an ad-hoc full-network check —
see project history) with errors around 1e-10 to 1e-12. The architecture
itself is not the risk here.

**What it actually learned — the honest result**: a direct sanity check
(perturbing lore totals and re-evaluating) confirmed the network learned
genuine, correctly-signed structure: raising *my* lore raises the
predicted value, raising the *opponent's* lore lowers it. That's real
signal, not noise.

But standalone play (`NeuralBot`, one-ply greedy-by-value) lost every
game in two separate batches against RandomBot (0/3, then 0/10). Tracing
its actual decisions turn-by-turn (same method used earlier to diagnose
SearchBot's ink-tie issue) found a clear, specific failure: **it chooses
"end turn" over playing a character or questing almost every single
time**, even with characters sitting unplayed in hand for 18+ turns
straight. Adding an explicit "whose turn is it" feature to the encoding
(a reasonable hypothesis — the original encoding had no such signal, so
a passed-vs-continued state looked confusingly similar) did not fix it;
if anything the retrained network ended turns even faster. That rules out
the specific hypothesis and points at something more basic: **2,680
training examples, generated by weak bots (Random/Heuristic), sampled at
random mid-game points and regressed against the final win/loss, is a
very noisy, weak training signal** — easy for a small network to latch
onto a locally-consistent but globally wrong shortcut ("passing" may
simply have been underrepresented or confounded in so small a sample)
rather than learn the real tradeoff between spending resources now and
holding them.

**Current status: not recommended for use.** `SearchBot` with its
hand-written evaluation remains the project's working default. `neural`
and `search_neural` are registered as bot types in `self_play.py` and
fully wired up, but produce weak/degenerate play until retrained
properly — don't read a `search_neural` loss as "the paper-based
approach doesn't work," read it as "this particular training run didn't
have enough signal yet," which the evidence above actually supports.

### Concrete next steps, in order
1. **Far more self-play data** — thousands of games, not hundreds. 2,680
   examples for a sequence model is simply thin; this is the most likely
   first fix.
2. **A less noisy training target.** Regressing a single mid-game
   snapshot against the final win/loss is a weak, high-variance signal.
   TD-learning / bootstrapping (train each state's value toward the
   *next* sampled state's value, not just the final outcome — the
   standard approach in AlphaZero-style self-play) would likely help a
   lot more than just adding more of the same kind of noisy examples.
3. **A degenerate-solution check built into training itself** — e.g.,
   automatically re-run the "does it ever quest" trace every few epochs
   and flag it, rather than only discovering this via manual inspection
   after the fact.
4. Once `search_neural` is actually competitive with plain `SearchBot`,
   revisit the deck1-vs-deck2 question again with it.

## Known gaps (the real work is here)

This is the honest list of what's *not* done — each is a reasonable next
project session, not a bug:

1. **Most of the ~1,294 cards' unique triggered abilities and action-card
   effect texts are still not scripted** — only the ~21 cards across the
   two decklists provided this session have handlers in
   `card_effects.py`. Everything else's text is present as structured
   data (`Card.abilities`, `Card.full_text`) but not executed —
   unscripted action cards resolve as a no-op (still cost their ink,
   still go to discard) and unscripted triggered abilities never fire.
   This is the long tail — script whatever deck you're testing next,
   following the patterns already in `card_effects.py`.
2. **No "the bag."** The real game queues triggered abilities and lets
   players choose resolution order (7.7). The engine currently resolves
   each triggered ability immediately/synchronously as it fires, which
   is observably different from the real game only in edge cases with
   multiple simultaneous triggers needing player-chosen ordering — none
   of the 21 scripted cards hit that edge case, but a future card might.
3. **The temporary-modifier ledgers only cover Strength and Lore.** Real
   card text can modify Willpower, grant/remove keywords temporarily,
   forbid actions ("can't be challenged"), etc. Extend
   `delayed_strength_mods`'s pattern (or add a sibling ledger) as new
   effects need it, rather than building a separate one-off mechanism
   per card.
4. **Shift variants Duo/Combo/Temporary/Potato Shift aren't implemented**
   (only base Shift, `[Classification] Shift`, `Universal Shift`).
   Cards with these keywords simply can't use them yet (`_has_unsupported_shift`
   filters them out of legal actions); they can still be played for
   their normal ink cost.
5. **Target selection only handles "pick one from a list."** "Choose up
   to N," multi-step "opponent then chooses," and damage-calculation-
   then-choice ordering (6.7.2 in the rules) aren't built out — every
   handler in `card_effects.py` so far only needed single choices.
6. **Sing Together is a simplification.** The real rule lets you pick
   *any subset* of ready dry characters whose combined cost meets the
   threshold; this engine currently offers one action that sings with
   *all* eligible characters at once rather than enumerating subsets
   (combinatorial otherwise). Fine for a bot that doesn't need the
   unused characters for anything else this turn; wrong if it does.
7. **No mulligan step.** Setup draws 7 and starts the game; `2.2.2`
   (looking at your hand and replacing any number of cards before the
   game begins) isn't implemented. `GameConfig.allow_mulligan` exists as
   a placeholder flag but currently does nothing.
8. **Deck construction is legality-only, not strategic**
   (`deck_utils.build_random_deck` — random colors, random fill, no
   curve-shaping or synergy). Real decks (see `data/decklists/`) are
   loaded via `decklist.py` instead — that's a parser, not a builder.
   Actual deckbuilding intelligence is still the roadmap's step 3.
9. **Multiplayer (`section 9`) and Ward's actual interaction with
   targeted effects are unimplemented.** No scripted card here targets
   through Ward, so this hasn't been exercised yet.
10. **A few specific simplifications** are called out where they happen —
    see "Interpretation calls made while scripting" above.

## Suggested next steps, in order

1. Script more decks/cards the same way `card_effects.py` does — pick a
   deck, dump its cards' full text (see this session's transcript for
   the pattern: `CardDatabase.find_by_full_name` + inspect `.abilities`),
   and add handlers. Watch for the same category of "universal rule
   embedded in one card's reminder text" bug the singing fix caught —
   double-check anything that looks like a keyword against the
   Comprehensive Rules section it cites before assuming the card text
   alone tells the whole story.
2. Once a decent slice of cards have real effects, replace
   `HeuristicBot` with a real search-based bot (minimax/MCTS) — the
   engine is perfect-information by default (open hands/decks), which
   makes this tractable without hidden-information modeling. The deck1-
   vs-deck2 win-rate skew above is a good first test case: does a
   smarter bot close the gap?
3. Only after the engine covers enough real cards to matter: build the
   actual deck-optimization loop (genetic algorithm over legal decks,
   fitness = self-play win rate).

## Validation done so far

840+ self-play games total across this project's two sessions:
440+ with random 2-color decks (random-vs-random and heuristic-vs-
heuristic), and 400+ using the two real 60-card decklists in
`data/decklists/` against each other (both bot types) — zero crashes,
zero unresolved (turn-limit) games throughout. A verbose single game
between the two real decks was manually inspected end-to-end and
confirmed correct firing/ordering for: Hades' deal, Mike Wazowski's
Find a Friend, Aurora's discount, Grandmother Willow's per-turn
discount stacking with Lantern's activated discount, Demona's Stone by
Day lockout and on-play effect, Nani's look-and-take, Mowgli's discard,
Support, and Elinor's end-of-turn trigger. A targeted scenario also
confirmed the "may enter play exerted" choice (Bodyguard, Mickey Mouse)
is actually reachable as a legal action — an early version of this
session's work had it silently unreachable, caught by writing a direct
check rather than trusting the self-play log alone.

This covers "does the engine stay internally consistent and execute the
scripted card text correctly," not "does it play good Lorcana" — that's
what roadmap step 2 is for.
#   l o r c a n a - s i m  
 