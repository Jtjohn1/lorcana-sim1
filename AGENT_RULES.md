# Rules for every agent working on this repo (read fully before touching anything)

You are one of two coding agents (Claude, Codex) working unattended overnight, each in
its own git worktree. A human reviews both results in the morning. Your work is kept
only if the automated gate passes; otherwise it is thrown away.

## Project charter (from PROJECT_STATUS.md, decided by the owner; read it and PROJECT_AUDIT.md too)
Priority order: rules correctness > game-state correctness > automated tests > card
effects > search correctness > performance > learned AI > deck optimization.
The AI is downstream of the simulator. Do not do AI work.

## Hard rules
1. Do exactly ONE task: the task file you are pointed to. No extra refactors, no
   renames, no "while I'm here" fixes. Out-of-scope problems go in QUESTIONS.
2. Never guess a Lorcana rule. If the rule is not stated in the task, the repo, or the
   Comprehensive Rules text (if it is in the repo), do NOT implement a guess. Say so in
   QUESTIONS and implement only what is certain (or nothing).
3. Never add a silent no-op. Unsupported behaviour must be visible (a log line, an
   error, or a report), never quietly ignored.
4. Every bug you fix or behaviour you add gets a test. A test must be able to fail:
   before you finish, prove it (temporarily break the code, see the test fail, restore).
5. Never weaken, delete, skip or edit an existing test to make things pass. Only files
   named in the task's ALLOW_EDIT line may be edited among existing tests. If a real bug
   is discovered that is outside the task, write a `pytest.mark.xfail(strict=True,
   reason=...)` test for it instead of fixing it, and mention it in QUESTIONS.
6. Do not edit anything under `data/` (decklists, cards.json, raw data), `tasks/`,
   `AGENT_RULES.md`, `pytest.ini`, `requirements.txt`, `.gitignore` or
   `overnight_runner.py`. Do not "fix" invalid decklists.
7. No network access, no installing packages, no `git push`, no touching anything
   outside this directory. Use only the Python standard library, numpy, pytest.
8. Determinism matters: no use of the global `random` module; use seeded RNG objects.
9. Windows environment: run Python as `python`, write paths with forward slashes.
10. Be honest. Do not say tests pass unless you ran them and saw them pass.

## Verified Lorcana facts (do not contradict these; do not guess others)
- Core Constructed deck: at least 60 cards (no maximum), at most 4 copies per full name,
  at most 2 ink colours. A dual-ink card needs BOTH of its inks in the deck.
- The Comprehensive Rules text is NOT in this repo. Any other rule you are unsure of goes in QUESTIONS.

## How to verify
Run `python -m pytest -q --ignore=lorcana/nn` from the repository root. ALL existing
tests must still pass (132 at the time of writing; the runner prints the real baseline),
plus your new ones. A task may delete tests only if its file says ALLOWED_TEST_DROP.
If you get stuck after reasonable effort, stop. Leaving the repo unchanged and
explaining why is a perfectly good outcome.

## Final reply format (the human reads this in the morning)
End your final message with exactly these two sections:

DONE:
- what you changed, file by file, and the test command output summary

QUESTIONS:
- every uncertainty, rule you could not verify, out-of-scope problem you noticed,
  or decision the owner must make (write "none" if none)
