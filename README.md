# Lorcana Self-Play Simulator

A Python engine for Disney Lorcana that can play full legal games
autonomously, aimed at eventually running bot-vs-bot self-play to
evaluate and evolve decks (like a chess engine, but for Lorcana).

Built against Comprehensive Rules v2.3.0 (effective October 16, 2026)
and LorcanaJSON card data for sets 9–14 (Fabled through Hyperia City) —
1,558 cards total.

## Status: rules engine under active development

The simulator runs complete games for the mechanics and card effects it
implements. Many unique card abilities are still missing, so game results
do not yet represent the full card pool. See "Known gaps" before using
results for strategy or deck evaluation.

## Layout

```
data/
  raw/setdata_9.json ... setdata_14.json   # source LorcanaJSON dumps
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
  deck1.txt–deck11.txt  # supplied and exploratory 60-card decklists
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

`GameConfig.open_hands` and `open_decks` control what `GameState.view_for(player)`
reports. Both default to `False`; set both to `True` for deliberate
perfect-information experiments.

The engine and bots receive the full internal `GameState`. `RandomBot`
chooses among legal moves, and `HeuristicBot` scores public board state and
its own hand. In hidden mode, `SearchBot` averages each root action across
four sampled positions by default. It keeps the acting player's hand fixed,
shuffles that player's deck order, and shuffles the opponent's hidden hand
and deck together.

The search samples from the simulator's actual remaining card pool for each
player. It no longer sees the opponent's exact hand or draw order, but it
still knows the opponent's unseen card composition. `view_for()` is a
redacted data view, not a sandbox for custom bots that receive `GameState`.
## What's implemented

- Full turn structure: Start-of-Turn (ready / set / draw) → Main Phase →
  End-of-Turn, including queued Start, Ready, challenge, banishment,
  movement, ink-drop, and end-of-turn trigger hooks. Registered triggers
  use active-player ordering and their controller chooses resolution order.
- Turn actions: ink, play character/item/location/action, quest,
  challenge (character or location), move to a location, activate an
  activated ability, Boost, end turn.
- Deck/hand/play/inkwell/discard/**under** zones (the last for Boost —
  cards tucked under a character), with correct state resets on zone
  change.
- Win/loss conditions: 20+ lore, deck-out at end of turn.
- Game state checks: banishment via damage ≥ willpower (including
  discarding anything Boosted under a banished character), checked after
  actions and at the challenge declaration/damage checkpoints. Characters
  are unassigned when their location leaves play.
- Keywords implemented mechanically include **Rush, Evasive, Alert,
  Bodyguard, Challenger, Resist, Reckless, Support, Boost, all six Shift
  variants, Singer, Sing Together, Adventurous, and Ward**. Sing Together
  offers each legal subset of ready, dry characters. Ink-drop counters can
  be gained and spent alone or mixed with ink cards. Ward filters targets
  an opponent may choose while resolving an effect; it does not prevent
  challenges or effects that do not choose a target.
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
- **Two Strength-modifier ledgers**: `state.temp_strength_bonus`
  ("this turn," cleared at your End-of-Turn) and
  `state.delayed_strength_mods` ("until the start of your next turn,"
  survives the opponent's whole turn). Supported modifiers carry through
  Shift stacks.
- **Dynamic Lore/damage-source hooks**: `STATIC_LORE_MODIFIERS` (e.g.
  The Queen's Jealous Heart) and `STATIC_DAMAGE_SOURCE_OVERRIDE` (e.g.
  Dale's Spike Suit — challenge damage uses Willpower instead of
  Strength for that controller's characters).
- **`STATIC_READY_BLOCKERS`**: a card can prevent its own instance from
  readying based on a live condition (e.g. Demona's Stone by Day).
- **`lorcana/card_effects.py`** contains handlers for selected abilities in
  decklists 1–4, plus one Boost interaction. Some implementations are
  simplified or omit options; see the interpretation notes and Known gaps
  below before treating a listed card effect as fully faithful. This is the
  reference pattern for adding more handlers.
- **A narrow generic text fallback** resolves simple draw, lore, and
  ink-drop triggers/activated abilities, including optional draw, plus
  exact simple action effects. More complex or conditional effects still
  need scripts.

## Decks validated this session

`data/decklists/deck1.txt` through `deck4.txt` pass the resolver audit.
A 10-game HeuristicBot mirror smoke run on deck4 completed with no
turn-limit games. Earlier 400+ game results on decks1–2 predate this rules
expansion and do not establish complete card-effect fidelity.
One thing worth flagging honestly rather than burying: with
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
`copy.deepcopy`, since the latter would duplicate the ~1,558-card
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

### New decklists (coverage in progress)

`data/decklists/deck3.txt` through `deck11.txt` — nine more 60-card
decks, all fully present in the sets 9–14 database (no missing cards).
Decks1–4 pass the resolver audit (22/22, 18/18, 17/17, and 19/19 unique
cards). Decks5–11 still contain unsupported text. Run
`check_card_coverage.py --deck ... --require-resolved-effects` before using
a deck for fitness experiments.

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

The engine still needs more work before large self-play results can be
trusted:

1. **Most unique card abilities are not scripted.** The database contains
   1,558 cards, but only a subset of their printed effects have resolvers.
   Exact generic templates cover common draw/discard, damage, healing,
   banish, and other simple effects. Unrecognized action effects still
   resolve as no-ops, and unrecognized triggers do not fire.
2. **Triggered-ability infrastructure is partial.** Registered triggers
   are queued and resolve in active-player order, with their controller
   choosing the order. Start, Ready, challenge, banishment, and end-of-turn
   timing windows are represented. Most card texts still need handlers, and
   simultaneous multi-step effects need more scenario coverage.
3. **Temporary modifiers cover only some characteristics.** Strength, Lore,
   Challenger, and Resist have selected temporary-effect support. General
   temporary changes to Willpower, keywords, and action restrictions are
   still missing.
4. **Deck evolution is limited.** Its mutation pool now excludes cards with
   unresolved text, and it refuses seed/opponent decks with unresolved effects.
   Decks1–4 pass the resolver-presence gate (22/22, 18/18, 17/17, and
   19/19 unique cards); the audit cannot certify handler fidelity.
5. **Multiplayer is not implemented.** Games currently have two players.
6. **Hidden-card sampling still knows the opponent's unseen pool.**
   SearchBot samples unknown hand/deck positions, but uses the simulator's
   actual remaining card multiset as its prior. It needs a public-evidence
   deck-composition model for unknown opposing decklists. Custom bots still
   receive the full `GameState`.`
7. **Some card effects and ordering choices need more coverage.** The
   engine has a choice hook, but not every multi-step choice and resolution
   process is represented yet.

### Card-effect coverage

Run `check_card_coverage.py` to audit the sets 9–14 database or a specific
decklist. It reports whether each structured ability and action text has a
known engine resolver; it does not certify that each handler is fully faithful.
Currently, 407 of 1,558 card records have a resolver for all their parsed
text. Decks1–4 pass the audit for all unique cards (22/22, 18/18, 17/17,
and 19/19), but this only confirms that a resolver is registered; it does
not confirm that every interaction matches the Comprehensive Rules.

### Deck evolution runner

`deck_evolution.py` checks deck legality and refuses to score a seed or
opponent deck that contains unresolved card text. By default, its mutation
pool includes every card with a known resolver across sets 9–14; `--pool`
can restrict it to one or more decklists. Decks1–4 pass the narrow resolver
gate, but global card coverage and bot-play validation remain incomplete,
so overnight fitness runs are not ready.

Once the chosen decks pass the coverage audit, the bounded runner can be
started with one generation, a small population, and paired games. Use
`--seed-deck`, `--opponent-deck`, `--pool`, `--bot`, and `--output` to configure it.

## Suggested next steps, in order

1. Script more decks/cards the same way `card_effects.py` does — pick a
   deck, dump its cards' full text (see this session's transcript for
   the pattern: `CardDatabase.find_by_full_name` + inspect `.abilities`),
   and add handlers. Watch for the same category of "universal rule
   embedded in one card's reminder text" bug the singing fix caught —
   double-check anything that looks like a keyword against the
   Comprehensive Rules section it cites before assuming the card text
   alone tells the whole story.
2. Improve SearchBot's hidden-information handling. It now samples unknown
   card positions for root decisions, but still assumes the opponent's true
   remaining card pool and sub-decision hooks receive the full `GameState`.
3. Grow the scripted card pool, then use the bounded evolution runner
   across multiple opponents and balanced seat assignments before any
   long optimization run.

## Validation done so far

Focused engine-rule checks pass, covering sets 9–14, ink-drop payments,
owner-turn drying, Shift variants, Sing Together subsets, Adventurous,
Ward, opening-hand replacement, triggered timing, bot action legality,
and legal deck mutation. Earlier project work also recorded 840+ self-play
games across the two included 60-card decks and legality-only random decks:
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
#