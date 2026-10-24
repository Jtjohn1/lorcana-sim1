#!/usr/bin/env python3
"""Overnight multi-agent runner for the Lorcana simulator.

Runs each coding agent (Claude Code, Codex CLI, ...) in its OWN git worktree/branch,
one task at a time, in parallel across agents. After every task the work is gated:

  * the agent process must exit cleanly within the time limit
  * it must not touch protected files or edit/delete existing tests
    (except files named on the task's ALLOW_EDIT line)
  * the full test suite must pass, with at least as many passing tests as at the start

Green  -> committed on the agent's branch.   Red -> thrown away (reset --hard).
Nothing is ever merged into your current branch; you compare branches in the morning.

Usage (from the project root, working tree clean):
    python overnight_runner.py --check
    python overnight_runner.py --only 03 --hours 1          # short daytime trial
    python overnight_runner.py --hours 8 --obsidian-dir "C:/Users/Johnl/Vault/Lorcana"
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--ignore=lorcana/nn"]

# Files/dirs an agent may never change (checked after every task).
PROTECTED = ("data/", "tasks/", "AGENT_RULES.md", "overnight_runner.py",
             "overnight_agents.json", "pytest.ini", "requirements.txt", ".gitignore")

# Command templates. "{prompt}" is replaced with the task prompt. THESE FLAGS CHANGE
# BETWEEN VERSIONS: check `claude --help` and `codex exec --help`, or override
# everything in overnight_agents.json (same shape: {"name": ["cmd", "arg", ...]}).
DEFAULT_AGENTS = {
    "claude": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits",
               "--allowedTools", "Read", "Edit", "Write",
               "Bash(python:*)", "Bash(git status:*)", "Bash(git diff:*)"],
    "codex": ["codex", "exec", "--full-auto", "{prompt}"],
}

PROMPT = (
    "You are working unattended overnight in a git worktree of the Lorcana simulator. "
    "First read AGENT_RULES.md in the current directory and follow it exactly. "
    "Then complete the single task described in {task} (read it fully). "
    "Run the tests yourself before finishing. End your final reply with the two "
    "sections DONE: and QUESTIONS: as AGENT_RULES.md describes."
)

LOCK = threading.Lock()


def log(msg: str) -> None:
    with LOCK:
        print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def sh(cmd, cwd, timeout=None):
    """Run a command, return (returncode, combined output). Never raises."""
    cmd = list(cmd)
    exe = shutil.which(cmd[0])
    if exe:
        cmd[0] = exe
    try:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                             errors="replace")
    except OSError as e:
        return 127, f"could not start {cmd[0]}: {e}"
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out or ""
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
        try:
            out, _ = p.communicate(timeout=20)
        except Exception:
            out = ""
        return 124, (out or "") + "\n[TIMEOUT: process killed]"


def git(args, cwd):
    return sh(["git", *args], cwd)


def run_tests(cwd):
    """Returns (ok, passed_count, output)."""
    code, out = sh(PYTEST, cwd, timeout=1800)
    m = re.search(r"(\d+) passed", out)
    passed = int(m.group(1)) if m else 0
    bad = code != 0 or re.search(r"\d+ (failed|error)", out) is not None
    return (not bad and passed > 0), passed, out


def parse_allow_edit(task_path: Path):
    for line in task_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("ALLOW_EDIT:"):
            return {p.strip() for p in line[len("ALLOW_EDIT:"):].split(",") if p.strip()}
    return set()


def check_changes(wt: Path, before: str, allow: set):
    """Stage everything, compare to `before`. Returns (changed_files, violations)."""
    git(["add", "-A"], wt)
    rc, out = git(["diff", "--cached", "--name-status", before], wt)
    changed, violations = [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, paths = parts[0][0], parts[1:]
        for path in paths:
            changed.append(path)
            if path in allow:
                continue
            if any(path == p or path.startswith(p) for p in PROTECTED):
                violations.append(f"protected file changed: {path}")
            elif path.startswith("tests/") and status != "A":
                violations.append(f"existing test file modified/deleted: {path}")
    return changed, violations


def tail(text: str, n: int = 40) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


class Report:
    def __init__(self, run_dir: Path, obsidian_dir: Path | None, stamp: str):
        self.rows, self.run_dir, self.obsidian_dir, self.stamp = [], run_dir, obsidian_dir, stamp
        self.notes = {}

    def add(self, row, final_text):
        with LOCK:
            self.rows.append(row)
            self.notes[(row["agent"], row["task"])] = tail(final_text, 40)
        self.write()

    def write(self):
        with LOCK:
            lines = [f"# Overnight run {self.stamp}", "",
                     "| agent | task | result | tests | seconds | detail |",
                     "|---|---|---|---|---|---|"]
            for r in self.rows:
                lines.append(f"| {r['agent']} | {r['task']} | {r['result']} | {r['passed']} | "
                             f"{r['seconds']} | {r['detail']} |")
            lines += ["", "## Agent final messages (last 40 lines each)", ""]
            for (agent, task), text in self.notes.items():
                lines += [f"### {agent} - {task}", "```", text, "```", ""]
            body = "\n".join(lines)
            (self.run_dir / "summary.md").write_text(body, encoding="utf-8")
            if self.obsidian_dir:
                try:
                    self.obsidian_dir.mkdir(parents=True, exist_ok=True)
                    (self.obsidian_dir / f"Lorcana overnight {self.stamp}.md").write_text(
                        body, encoding="utf-8")
                except OSError as e:
                    print(f"(could not write to Obsidian dir: {e})")


def run_task(agent, cmd_t, wt, task, baseline, timeout_s, run_dir, push, branch, report):
    t0 = time.time()
    name = Path(task).stem
    _, before = git(["rev-parse", "HEAD"], wt)
    before = before.strip()
    allow = parse_allow_edit(wt / task)
    prompt = PROMPT.format(task=task)
    cmd = [prompt if a == "{prompt}" else a for a in cmd_t]

    log(f"{agent}: starting {name}")
    rc, out = sh(cmd, wt, timeout=timeout_s)
    adir = run_dir / agent
    adir.mkdir(parents=True, exist_ok=True)
    (adir / f"{name}.log").write_text(out, encoding="utf-8")

    def finish(result, passed, detail):
        row = dict(agent=agent, task=name, result=result, passed=passed,
                   seconds=int(time.time() - t0), detail=detail)
        log(f"{agent}: {name} -> {result} ({detail})")
        report.add(row, out)

    def revert():
        git(["reset", "--hard", before], wt)
        git(["clean", "-fd"], wt)

    if rc != 0:
        revert()
        return finish("REVERTED", "-", f"agent exit code {rc}" + (" (timeout)" if rc == 124 else ""))

    changed, violations = check_changes(wt, before, allow)
    if violations:
        revert()
        return finish("REVERTED", "-", "; ".join(violations)[:300])
    if not changed:
        return finish("NO CHANGE", baseline, "agent made no changes (see its notes)")

    ok, passed, tout = run_tests(wt)
    (adir / f"{name}.pytest.log").write_text(tout, encoding="utf-8")
    if not ok:
        revert()
        return finish("REVERTED", passed, "tests failed after agent's change")
    if passed < baseline:
        revert()
        return finish("REVERTED", passed, f"fewer passing tests than baseline ({baseline})")

    rc, _ = git(["diff", "--cached", "--quiet"], wt)
    if rc != 0:  # staged changes not yet committed (agent did not commit itself)
        rc, cout = git(["-c", "user.name=overnight", "-c", "user.email=overnight@local",
                        "commit", "-m", f"{agent}: {name}\n\nAutomated overnight run; "
                        f"{passed} tests passing."], wt)
        if rc != 0:
            revert()
            return finish("REVERTED", passed, f"commit failed: {tail(cout, 3)}")
    detail = f"{len(changed)} files"
    if push:
        prc, pout = git(["push", "-u", "origin", branch], wt)
        detail += "; pushed" if prc == 0 else f"; PUSH FAILED: {tail(pout, 2)}"
    finish("KEPT", passed, detail)


def agent_worker(agent, cmd_t, stamp, tasks, baseline, deadline, timeout_s, run_dir, push, report):
    branch = f"overnight/{stamp}/{agent}"
    wt = ROOT.parent / f"{ROOT.name}_wt_{agent}_{stamp}"
    rc, out = git(["worktree", "add", "-b", branch, str(wt), "HEAD"], ROOT)
    if rc != 0:
        log(f"{agent}: could not create worktree: {tail(out, 3)}")
        report.add(dict(agent=agent, task="setup", result="FAILED", passed="-", seconds=0,
                        detail="worktree creation failed"), out)
        return
    log(f"{agent}: worktree {wt} on branch {branch}")
    for task in tasks:
        if time.time() > deadline:
            log(f"{agent}: deadline reached, stopping")
            break
        try:
            run_task(agent, cmd_t, wt, task, baseline, timeout_s, run_dir, push, branch, report)
        except Exception as e:  # never let one task kill the night
            log(f"{agent}: runner error on {task}: {e!r}")
            git(["reset", "--hard"], wt)
            git(["clean", "-fd"], wt)
            report.add(dict(agent=agent, task=Path(task).stem, result="RUNNER ERROR", passed="-",
                            seconds=0, detail=repr(e)[:200]), "")


def keep_awake(on: bool):
    if sys.platform == "win32":
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED if on else ES_CONTINUOUS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agents", default=None, help="comma list; default = all configured")
    ap.add_argument("--hours", type=float, default=8.0, help="stop starting new tasks after this long")
    ap.add_argument("--task-minutes", type=float, default=45.0, help="kill an agent after this long per task")
    ap.add_argument("--only", default=None, help="only run task files whose name contains this (e.g. 03)")
    ap.add_argument("--obsidian-dir", default=None, help="also write the summary note into this folder")
    ap.add_argument("--push", action="store_true", help="git push each agent branch after each kept task")
    ap.add_argument("--check", action="store_true", help="verify setup and exit without running agents")
    args = ap.parse_args()

    agents = dict(DEFAULT_AGENTS)
    cfg = ROOT / "overnight_agents.json"
    if cfg.exists():
        agents = json.loads(cfg.read_text(encoding="utf-8"))
    if args.agents:
        agents = {k: v for k, v in agents.items() if k in args.agents.split(",")}

    tasks = sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "tasks").glob("*.md"))
    if args.only:
        tasks = [t for t in tasks if args.only in t]

    problems = []
    if git(["rev-parse", "--is-inside-work-tree"], ROOT)[0] != 0:
        problems.append("this folder is not a git repository")
    else:
        rc, out = git(["status", "--porcelain"], ROOT)
        if out.strip():
            problems.append("working tree is not clean; commit or stash first:\n" + tail(out, 10))
        if git(["rev-parse", "HEAD"], ROOT)[0] != 0:
            problems.append("repository has no commits yet")
    for needed in ("AGENT_RULES.md", "tests"):
        if not (ROOT / needed).exists():
            problems.append(f"missing {needed}")
    if not tasks:
        problems.append("no task files found in tasks/")
    for name, cmd in agents.items():
        if shutil.which(cmd[0]) is None:
            problems.append(f"agent '{name}': command '{cmd[0]}' not found on PATH")

    print(f"agents: {list(agents)}\ntasks: {tasks}")
    ok, baseline, out = run_tests(ROOT)
    print(f"baseline tests: {'OK' if ok else 'FAILING'} ({baseline} passed)")
    if not ok:
        problems.append("baseline test suite is not green:\n" + tail(out, 15))
    if problems:
        print("\nPROBLEMS:\n- " + "\n- ".join(problems))
        return 1
    if args.check:
        print("\nSetup looks good.")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    run_dir = ROOT / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    obs = Path(args.obsidian_dir) if args.obsidian_dir else None
    report = Report(run_dir, obs, stamp)
    deadline = time.time() + args.hours * 3600

    keep_awake(True)
    try:
        threads = [threading.Thread(target=agent_worker, name=a, daemon=False, args=(
            a, cmd, stamp, tasks, baseline, deadline, args.task_minutes * 60, run_dir,
            args.push, report)) for a, cmd in agents.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        keep_awake(False)
        report.write()
    print(f"\nDone. Summary: {run_dir / 'summary.md'}")
    print("Branches: " + ", ".join(f"overnight/{stamp}/{a}" for a in agents))
    return 0


if __name__ == "__main__":
    sys.exit(main())
