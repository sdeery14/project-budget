"""The two helpers behind the /round skill, with ``gh`` replaced by canned answers.

``scripts/pr_wait.py`` decides when a PR has settled: every required check finished and
Codex answered for the head commit. ``scripts/review_threads.py`` lists the unresolved
review threads and resolves them. Neither is a package, so they are loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
HEAD = "f247b067b9e51ae40e04b12bcc9a23c94106c55d"
OLD = "3730e49b14e6505a9020efc869934adad96902a0"
PUSHED_AT = "2026-11-15T19:10:00Z"
REQUIRED = ["Lint and types", "Tests (SQLite)"]


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pr_wait = load("pr_wait")
review_threads = load("review_threads")


def codex_comment(body: str, created_at: str) -> dict[str, Any]:
    return {"user": {"login": pr_wait.CODEX_LOGIN}, "body": body, "created_at": created_at}


def state_comment(commit: str, *, findings: bool, created_at: str) -> dict[str, Any]:
    state = {
        "code_review": {
            "commit_sha": commit,
            "has_any_findings": findings,
            "has_unresolved_findings": findings,
            "status": "completed",
        }
    }
    body = f"<!-- codex-review-state:v1 {json.dumps(state)} -->\n<details>summary</details>"
    return codex_comment(body, created_at)


NOTICE = "To use Codex here, [create an environment for this repo](https://example.invalid)."


# --- checks ---------------------------------------------------------------------------------


def test_check_states_cover_both_check_kinds() -> None:
    rollup = [
        {
            "__typename": "CheckRun",
            "name": "Lint and types",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
        },
        {
            "__typename": "CheckRun",
            "name": "Tests (SQLite)",
            "status": "IN_PROGRESS",
            "conclusion": None,
        },
        {
            "__typename": "CheckRun",
            "name": "UI smoke",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
        },
        {"__typename": "StatusContext", "context": "Security scans", "state": "SUCCESS"},
        {"__typename": "StatusContext", "context": "Container", "state": "PENDING"},
        {"__typename": "StatusContext", "context": "External", "state": "ERROR"},
    ]
    assert pr_wait.check_states(rollup) == {
        "Lint and types": "pass",
        "Tests (SQLite)": "pending",
        "UI smoke": "fail",
        "Security scans": "pass",
        "Container": "pending",
        "External": "fail",
    }


def test_ci_verdict_needs_every_required_check_and_fails_fast() -> None:
    assert pr_wait.ci_verdict({"Lint and types": "pass"}, REQUIRED) == "pending"
    assert (
        pr_wait.ci_verdict({"Lint and types": "pass", "Tests (SQLite)": "pass"}, REQUIRED) == "pass"
    )
    # One failure anywhere decides, even while required checks are still running.
    states = {"Lint and types": "pending", "Tests (SQLite)": "pass", "Extra": "fail"}
    assert pr_wait.ci_verdict(states, REQUIRED) == "fail"
    # A missing required check is pending, not passing.
    assert pr_wait.ci_verdict({"Tests (SQLite)": "pass"}, REQUIRED) == "pending"


# --- Codex state ----------------------------------------------------------------------------


def with_comments(monkeypatch: pytest.MonkeyPatch, comments: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(pr_wait, "issue_comments", lambda number: comments)


def test_issue_comments_follow_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Codex answer posted after the hundredth comment must still be seen."""
    pages = {
        1: [{"id": i} for i in range(100)],
        2: [{"id": i} for i in range(100, 200)],
        3: [{"id": 200}],
    }
    asked: list[str] = []

    def fake_gh(*args: str) -> Any:
        asked.append(args[1])
        page = int(args[1].rsplit("page=", 1)[1])
        return pages.get(page, [])

    monkeypatch.setattr(pr_wait, "gh", fake_gh)
    comments = pr_wait.issue_comments(64)
    assert [c["id"] for c in comments] == list(range(201))
    assert len(asked) == 3
    assert all("/issues/64/comments?per_page=100&page=" in url for url in asked)


def test_head_arrival_is_the_check_suite_creation_not_a_run_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pushed 19:10 (suites created), runners busy until 19:15: the boundary is the push."""
    answers = {
        f"repos/{{owner}}/{{repo}}/commits/{HEAD}/check-suites?per_page=100": {
            "check_suites": [
                {"app": {"slug": "github-actions"}, "created_at": "2026-11-15T19:10:05Z"},
                {"app": {"slug": "other-app"}, "created_at": "2026-11-15T19:10:02Z"},
                {"app": {"slug": "odd"}, "created_at": None},
            ]
        }
    }
    monkeypatch.setattr(pr_wait, "gh", lambda *args: answers[args[1]])
    assert pr_wait.head_arrived_at(HEAD) == "2026-11-15T19:10:02Z"


def test_a_notice_posted_while_ci_is_queued_counts_for_the_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pushed 19:10, quota notice 19:11, first job starts 19:15: the notice is the answer."""
    with_comments(
        monkeypatch,
        [
            state_comment(OLD, findings=True, created_at="2026-11-15T18:50:00Z"),
            codex_comment("Quota exceeded for this workspace.", "2026-11-15T19:11:00Z"),
        ],
    )
    state = pr_wait.codex_state(64, HEAD, "2026-11-15T19:10:02Z")
    assert state == {"status": "error", "message": "Quota exceeded for this workspace."}


def test_head_arrival_falls_back_to_statuses_then_to_now(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: dict[str, Any] = {
        f"repos/{{owner}}/{{repo}}/commits/{HEAD}/check-suites?per_page=100": {"check_suites": []},
        f"repos/{{owner}}/{{repo}}/commits/{HEAD}/status": {
            "statuses": [{"created_at": "2026-11-15T19:13:30Z"}]
        },
    }
    monkeypatch.setattr(pr_wait, "gh", lambda *args: answers[args[1]])
    assert pr_wait.head_arrived_at(HEAD) == "2026-11-15T19:13:30Z"

    answers[f"repos/{{owner}}/{{repo}}/commits/{HEAD}/status"] = {"statuses": []}
    before = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    arrived = pr_wait.head_arrived_at(HEAD)
    assert arrived >= before  # nothing posted before now can be about a push with no suites yet


def test_a_notice_between_commit_and_push_is_about_the_old_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Committed 19:00, Codex notice about the old head 19:05, pushed 19:10."""
    with_comments(
        monkeypatch,
        [
            state_comment(OLD, findings=True, created_at="2026-11-15T18:50:00Z"),
            codex_comment(NOTICE, "2026-11-15T19:05:00Z"),
        ],
    )
    state = pr_wait.codex_state(64, HEAD, "2026-11-15T19:10:00Z")
    assert state is not None
    assert state["commit_sha"] == OLD  # the old review stands; the notice is not about HEAD


def test_state_block_for_the_head_commit_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    with_comments(
        monkeypatch,
        [
            {"user": {"login": "someone"}, "body": "@codex review", "created_at": PUSHED_AT},
            state_comment(HEAD, findings=False, created_at="2026-11-15T19:12:00Z"),
        ],
    )
    state = pr_wait.codex_state(64, HEAD, PUSHED_AT)
    assert state == {
        "commit_sha": HEAD,
        "has_any_findings": False,
        "has_unresolved_findings": False,
        "status": "completed",
    }


def test_nothing_before_the_first_codex_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    with_comments(monkeypatch, [])
    assert pr_wait.codex_state(64, HEAD, PUSHED_AT) is None


def test_a_notice_about_an_earlier_push_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    with_comments(monkeypatch, [codex_comment(NOTICE, "2026-11-15T18:59:37Z")])
    assert pr_wait.codex_state(64, HEAD, PUSHED_AT) is None


def test_a_notice_about_the_current_push_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with_comments(monkeypatch, [codex_comment(NOTICE, "2026-11-15T19:11:00Z")])
    state = pr_wait.codex_state(64, HEAD, PUSHED_AT)
    assert state == {"status": "error", "message": NOTICE}


def test_a_newer_notice_supersedes_a_state_about_an_older_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review of the first push, then a fix pushed, then a quota or environment notice."""
    with_comments(
        monkeypatch,
        [
            state_comment(OLD, findings=True, created_at="2026-11-15T19:00:00Z"),
            codex_comment(NOTICE, "2026-11-15T19:11:00Z"),
        ],
    )
    state = pr_wait.codex_state(64, HEAD, PUSHED_AT)
    assert state == {"status": "error", "message": NOTICE}


def test_a_state_for_the_head_is_not_undone_by_a_later_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_comments(
        monkeypatch,
        [
            state_comment(HEAD, findings=False, created_at="2026-11-15T19:12:00Z"),
            codex_comment("Quota notice", "2026-11-15T19:13:00Z"),
        ],
    )
    state = pr_wait.codex_state(64, HEAD, PUSHED_AT)
    assert state is not None
    assert state["commit_sha"] == HEAD
    assert state["status"] == "completed"


# --- settled --------------------------------------------------------------------------------


def snap(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "OPEN",
        "ci": "pass",
        "codex": {"status": "completed", "for_head": True, "unresolved": False},
    }
    base.update(overrides)
    return base


def test_settled_when_checks_and_codex_are_done_for_the_head() -> None:
    assert pr_wait.settled(snap(), until_merged=False)
    assert not pr_wait.settled(snap(ci="pending"), until_merged=False)
    assert not pr_wait.settled(snap(codex=None), until_merged=False)
    older = {"status": "completed", "for_head": False, "unresolved": False}
    assert not pr_wait.settled(snap(codex=older), until_merged=False)
    running = {"status": "running", "for_head": True, "unresolved": None}
    assert not pr_wait.settled(snap(codex=running), until_merged=False)


def test_settled_on_any_terminal_state() -> None:
    assert pr_wait.settled(snap(ci="fail", codex=None), until_merged=False)
    assert pr_wait.settled(snap(codex={"status": "error", "for_head": False}), until_merged=False)
    assert pr_wait.settled(snap(state="MERGED", ci="pending", codex=None), until_merged=False)
    assert pr_wait.settled(snap(state="CLOSED", ci="pending", codex=None), until_merged=True)


def test_until_merged_waits_through_a_clean_review() -> None:
    assert not pr_wait.settled(snap(), until_merged=True)
    assert pr_wait.settled(snap(ci="fail"), until_merged=True)


# --- review threads -------------------------------------------------------------------------


def thread(
    thread_id: str, *, resolved: bool, body: str = "**P2** Something", outdated: bool = False
) -> dict[str, Any]:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": "scripts/pr_wait.py",
        "line": 87 if not outdated else None,
        "originalLine": 80,
        "comments": {
            "nodes": [
                {
                    "author": {"login": "chatgpt-codex-connector"},
                    "body": body,
                    "url": f"https://example.invalid/{thread_id}",
                    "createdAt": PUSHED_AT,
                }
            ]
        },
    }


def page(nodes: list[dict[str, Any]], *, cursor: str | None) -> dict[str, Any]:
    info = {"hasNextPage": cursor is not None, "endCursor": cursor}
    return {"repository": {"pullRequest": {"reviewThreads": {"pageInfo": info, "nodes": nodes}}}}


def test_unresolved_threads_follow_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hundred resolved threads on page one must not hide an open one on page two."""
    pages = {
        None: page([thread(f"T{i}", resolved=True) for i in range(100)], cursor="c1"),
        "c1": page([thread("T100", resolved=False, body="**P1** Still open")], cursor=None),
    }
    calls: list[dict[str, Any]] = []

    def fake_graphql(query: str, **variables: Any) -> Any:
        calls.append(variables)
        return pages[variables.get("after")]

    monkeypatch.setattr(review_threads, "repo", lambda: ("owner", "name"))
    monkeypatch.setattr(review_threads, "graphql", fake_graphql)

    result = review_threads.unresolved_threads(64)

    assert [c.get("after") for c in calls] == [None, "c1"]
    assert [t["id"] for t in result] == ["T100"]
    assert result[0]["severity"] == "P1"
    assert result[0]["line"] == 87
    assert result[0]["replies"] == 0


def test_an_outdated_thread_keeps_its_original_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_threads, "repo", lambda: ("owner", "name"))
    monkeypatch.setattr(
        review_threads,
        "graphql",
        lambda query, **variables: page(
            [thread("T1", resolved=False, body="no severity tag", outdated=True)], cursor=None
        ),
    )
    (only,) = review_threads.unresolved_threads(64)
    assert only["outdated"] is True
    assert only["line"] == 80
    assert only["severity"] is None


def test_resolve_replies_then_resolves_unless_kept_open(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    def fake_graphql(query: str, **variables: Any) -> Any:
        sent.append((query, variables))
        return {}

    monkeypatch.setattr(review_threads, "graphql", fake_graphql)
    monkeypatch.setattr(
        "sys.argv", ["review_threads.py", "resolve", "64", "T1", "--reply", "Fixed in abc1234"]
    )
    assert review_threads.main() == 0
    assert [q for q, _ in sent] == [review_threads.REPLY_MUTATION, review_threads.RESOLVE_MUTATION]
    assert sent[0][1] == {"thread": "T1", "body": "Fixed in abc1234"}
    assert sent[1][1] == {"thread": "T1"}

    sent.clear()
    monkeypatch.setattr(
        "sys.argv",
        ["review_threads.py", "resolve", "64", "T1", "--reply", "Disagree", "--keep-open"],
    )
    assert review_threads.main() == 0
    assert [q for q, _ in sent] == [review_threads.REPLY_MUTATION]
