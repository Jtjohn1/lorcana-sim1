# Task 04 - Regression test for the SearchBot node-budget bug (audit 5.3)

ALLOW_EDIT:

## Goal
Audit bug 5.3 (fixed earlier, currently unprotected): the node budget used to be
shared across sibling top-level candidates, so the first candidate consumed it and
the others were compared without real search. The fix resets the budget per top-level
candidate. Write the missing regression test. TEST ONLY: change no production code.

## Requirements
- Read `bots/search_bot.py` fully first. Work out how the budget is counted
  (`_nodes_visited`, `max_nodes`, `max_total_nodes`) and where it is reset.
- New file `tests/test_search_bot.py` using a small synthetic state from
  `tests/helpers.py` in which several top-level candidates exist (e.g. several
  characters in hand with enough ink, or several quest/challenge choices).
- The test must show that EVERY top-level candidate was actually searched below the
  root (not just statically evaluated), i.e. each candidate got its own budget. Use
  only existing public/semi-public hooks (the verbose/trace log, node counters); do
  not add code to production files.
- Mutation check (mandatory): temporarily reintroduce the bug in your working copy
  (make the budget counter shared / not reset per candidate), confirm your test FAILS,
  then restore the file exactly. Report that you did this in DONE. If you cannot make
  the test fail under the mutation, the test is worthless: do not commit it, and say
  so in QUESTIONS.
- The test must be fast (a couple of seconds) and deterministic (seeded).
- Do not touch the separate OPEN issue 6 (budget cutoff being global per candidate);
  you may mention it in QUESTIONS.
