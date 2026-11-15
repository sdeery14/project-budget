"""List a pull request's unresolved review threads, or reply to one and resolve it.

Used by the ``/round`` skill (``.claude/skills/round/SKILL.md``) to read Codex's inline
findings and to close each one with a note on what changed. Needs an authenticated
GitHub CLI (``gh``).

    uv run python scripts/review_threads.py list 64
    uv run python scripts/review_threads.py resolve 64 PRRT_kwDO... --reply "Fixed in abc1234: ..."
    uv run python scripts/review_threads.py resolve 64 PRRT_kwDO... --reply "Not changing: ..." --keep-open

``list`` prints a JSON array; each thread carries its id, path, line, whether the diff moved
on from it, the author and body of the first comment, the severity tag (``P0`` to ``P3``) if the
body has one, the number of replies and the comment URL.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

SEVERITY = re.compile(r"\bP[0-3]\b")

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id isResolved isOutdated path line originalLine
          comments(first: 50) { nodes { author { login } body url createdAt } }
        }
      }
    }
  }
}
"""

REPLY_MUTATION = """
mutation($thread: ID!, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) {
    comment { url }
  }
}
"""

RESOLVE_MUTATION = """
mutation($thread: ID!) {
  resolveReviewThread(input: {threadId: $thread}) { thread { isResolved } }
}
"""


def graphql(query: str, **variables: str | int) -> Any:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    completed = subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(completed.stdout)["data"]


def repo() -> tuple[str, str]:
    completed = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name"], check=True, capture_output=True, text=True
    )
    view = json.loads(completed.stdout)
    return view["owner"]["login"], view["name"]


def unresolved_threads(number: int) -> list[dict[str, Any]]:
    owner, name = repo()
    data = graphql(THREADS_QUERY, owner=owner, name=name, number=number)
    threads = data["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    result: list[dict[str, Any]] = []
    for thread in threads:
        if thread["isResolved"]:
            continue
        comments = thread["comments"]["nodes"]
        first = comments[0] if comments else {}
        body = first.get("body") or ""
        severity = SEVERITY.search(body)
        result.append(
            {
                "id": thread["id"],
                "path": thread["path"],
                "line": thread["line"] or thread["originalLine"],
                "outdated": thread["isOutdated"],
                "author": (first.get("author") or {}).get("login"),
                "severity": severity.group(0) if severity else None,
                "body": body,
                "replies": max(len(comments) - 1, 0),
                "url": first.get("url"),
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    lister = sub.add_parser("list", help="print the unresolved threads as JSON")
    lister.add_argument("number", type=int)
    resolver = sub.add_parser("resolve", help="reply to a thread and mark it resolved")
    resolver.add_argument("number", type=int)
    resolver.add_argument("thread_id")
    resolver.add_argument("--reply", required=True, help="what changed, or why nothing did")
    resolver.add_argument(
        "--keep-open", action="store_true", help="post the reply but leave the thread unresolved"
    )
    args = parser.parse_args()

    if args.command == "list":
        sys.stdout.write(json.dumps(unresolved_threads(args.number), indent=2) + "\n")
        return 0

    graphql(REPLY_MUTATION, thread=args.thread_id, body=args.reply)
    if not args.keep_open:
        graphql(RESOLVE_MUTATION, thread=args.thread_id)
    sys.stdout.write(f"{args.thread_id}: replied{'' if args.keep_open else ' and resolved'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
