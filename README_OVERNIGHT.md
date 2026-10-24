# Overnight kit - setup (Windows, PowerShell)

Extract this zip INTO your project root (the folder that contains `lorcana\`, `bots\`, `tests\`).
You get: overnight_runner.py, AGENT_RULES.md, tasks\ (4 task files), this README.

## One-time setup
1. Make sure the project is a git repo and everything is committed:
       git status
   (if "not a git repository":  git init ; git add -A ; git commit -m "baseline")
2. Add `runs/` to .gitignore (the runner writes its logs there), then commit.
3. Install the two agent CLIs and log in to each ONCE, interactively, so they don't
   stop to ask for a login at 3 a.m. Follow each tool's current install docs:
     - Claude Code  (command: claude)
     - OpenAI Codex CLI (command: codex)
   Both bill separately from chat subscriptions (API usage or their own plans).
4. Check the agent flags in overnight_runner.py (DEFAULT_AGENTS) against
   `claude --help` and `codex exec --help`. If they differ, create overnight_agents.json:
       {"claude": ["claude", "-p", "{prompt}", ...], "codex": ["codex", "exec", "{prompt}"]}
5. Verify everything:
       python overnight_runner.py --check

## Daytime trial (do this BEFORE leaving it overnight)
       python overnight_runner.py --only 03 --hours 1
Task 03 is the smallest. Read runs\<stamp>\summary.md and the agents' logs.

## The real run
       python overnight_runner.py --hours 8 --obsidian-dir "C:/path/to/your/vault/Lorcana"
Leave the PC plugged in. The runner asks Windows not to sleep, but also set
Power & sleep to "Never" while plugged in. Do not close the terminal.

## Morning
- runs\<stamp>\summary.md (also copied into Obsidian): what was KEPT / REVERTED and why,
  plus each agent's DONE / QUESTIONS notes.
- Each agent has its own branch: overnight/<stamp>/claude and overnight/<stamp>/codex.
  Compare:  git diff master..overnight/<stamp>/claude   (use your branch name)
  Merge only what you've reviewed. Answer the QUESTIONS before anything rules-related.

## What protects you
Worktrees (agents never touch your working folder), the test gate, protected files
(data/, tasks/, pytest.ini, ...), no editing of existing tests, per-task timeout.
What does NOT protect you: this is not an OS sandbox. The agents can run commands on
your PC with the permissions their flags allow. Keep the flags narrow, and run the
daytime trial first.
