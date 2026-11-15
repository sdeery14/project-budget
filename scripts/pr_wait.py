"""Wait for a pull request's required checks and its Codex review, then print one JSON line.

Used by the ``/round`` skill (``.claude/skills/round/SKILL.md``). Needs an authenticated
GitHub CLI (``gh``). Exit code 0 when the wait ended with an answer, 2 on timeout.

    uv run python scripts/pr_wait.py 64                 # checks and the Codex review of the head commit
    uv run python scripts/pr_wait.py 64 --until-merged  # the PR merged or closed (or a check failed)

The Codex GitHub app keeps one summary comment per PR with a hidden state block
(``codex-review-state:v1 {...}``) naming the reviewed commit and whether findings remain.
That block, not the comment text, is what this script reads.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from typing import Any

CODEX_LOGIN = "chatgpt-codex-connector[bot]"
STATE_BLOCK = re.compile(r"codex-review-state:v1 (\{.*?\}) -->", re.S)
PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def gh(*args: str) -> Any:
    """Run ``gh`` and parse its JSON output (``None`` when it printed nothing)."""
    completed = subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    )
    return json.loads(completed.stdout) if completed.stdout.strip() else None


def required_contexts() -> list[str]:
    return list(
        gh("api", "repos/{owner}/{repo}/branches/main/protection/required_status_checks/contexts")
    )


def check_states(rollup: list[dict[str, Any]]) -> dict[str, str]:
    """Map each check's name to ``pass``, ``fail`` or ``pending``."""
    states: dict[str, str] = {}
    for check in rollup:
        name = check.get("name") or check.get("context") or "?"
        if check.get("__typename") == "StatusContext":
            state = check.get("state")
            if state == "SUCCESS":
                states[name] = "pass"
            elif state in {"PENDING", "EXPECTED"}:
                states[name] = "pending"
            else:
                states[name] = "fail"
        elif check.get("status") != "COMPLETED":
            states[name] = "pending"
        else:
            states[name] = "pass" if check.get("conclusion") in PASSING else "fail"
    return states


def ci_verdict(states: dict[str, str], required: list[str]) -> str:
    """``fail`` as soon as any check fails; ``pass`` once every required check passed."""
    if any(state == "fail" for state in states.values()):
        return "fail"
    if all(states.get(name) == "pass" for name in required):
        return "pass"
    return "pending"


def codex_state(number: int) -> dict[str, Any] | None:
    """The latest Codex code-review state on the PR, or ``None`` before the first one."""
    comments = gh("api", f"repos/{{owner}}/{{repo}}/issues/{number}/comments?per_page=100") or []
    latest: dict[str, Any] | None = None
    for comment in comments:
        if comment.get("user", {}).get("login") != CODEX_LOGIN:
            continue
        match = STATE_BLOCK.search(comment.get("body") or "")
        if match:
            latest = json.loads(match.group(1)).get("code_review")
    return latest


def snapshot(number: int, required: list[str]) -> dict[str, Any]:
    pr = gh(
        "pr",
        "view",
        str(number),
        "--json",
        "headRefOid,state,mergedAt,isDraft,mergeStateStatus,statusCheckRollup",
    )
    states = check_states(pr.get("statusCheckRollup") or [])
    review = codex_state(number)
    head = pr["headRefOid"]
    codex: dict[str, Any] | None = None
    if review is not None:
        codex = {
            "status": review.get("status"),
            "commit": review.get("commit_sha"),
            "for_head": review.get("commit_sha") == head,
            "findings": review.get("has_any_findings"),
            "unresolved": review.get("has_unresolved_findings"),
        }
    return {
        "pr": number,
        "head": head,
        "state": pr["state"],
        "merged": pr.get("mergedAt") is not None,
        "draft": pr.get("isDraft", False),
        "merge_state": pr.get("mergeStateStatus"),
        "ci": ci_verdict(states, required),
        "checks": states,
        "codex": codex,
    }


def settled(snap: dict[str, Any], *, until_merged: bool) -> bool:
    if snap["state"] != "OPEN":
        return True
    if snap["ci"] == "fail":
        return True
    if until_merged:
        return False
    codex = snap["codex"]
    codex_done = codex is not None and codex["for_head"] and codex["status"] == "completed"
    return snap["ci"] == "pass" and codex_done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("number", type=int, help="pull request number")
    parser.add_argument(
        "--until-merged", action="store_true", help="wait for the PR to merge or close instead"
    )
    parser.add_argument("--timeout-minutes", type=float, default=45)
    parser.add_argument("--poll-seconds", type=float, default=30)
    args = parser.parse_args()

    required = required_contexts()
    deadline = time.monotonic() + args.timeout_minutes * 60
    while True:
        snap = snapshot(args.number, required)
        if settled(snap, until_merged=args.until_merged):
            sys.stdout.write(json.dumps(snap) + "\n")
            return 0
        if time.monotonic() >= deadline:
            snap["timeout"] = True
            sys.stdout.write(json.dumps(snap) + "\n")
            return 2
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
