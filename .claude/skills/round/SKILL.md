---
name: round
description: Work every open issue in a GitHub milestone unattended. One PR per issue, wait for CI and the Codex review, fix findings, arm auto-merge, and label anything that needs the owner. Use when the owner says "/round <milestone>" or asks to run a development round.
---

# /round <milestone> [--only 12,14] [--dry-run]

Run a round of development without the owner at the keyboard. The input is a milestone of
small, well-specified issues; the output is a merged milestone plus a short list of PRs that
need a human. The owner reads one summary when they come back.

Read `AGENTS.md` and `CONTRIBUTING.md` first. Everything there applies to every PR here.

## Hard limits

These are not judgment calls. Breaking one ends the round for that issue, with a
`needs-human` label and a comment saying why.

- Never tag, never push to `main`, never force-push, never change branch protection or
  repository settings, never edit `.github/workflows/release.yml`.
- Never change or add an ADR, and never move anything off the deferred list (ADR-011). An
  issue that needs either gets the label, not a workaround.
- Never arm auto-merge on a PR that changes `docs/adr/`, `.github/workflows/`,
  `src/projectbudget/persistence/migrations/`, `pyproject.toml` dependencies or
  `CHANGELOG.md` sections other than Unreleased. Open the PR, get it green and reviewed,
  then leave it for the owner with the label.
- Never merge over a failing check, an unresolved review thread or a Codex state that is
  not `completed` for the exact head commit.
- Never argue with the reviewer past two fix rounds on one PR. Label it and move on.
- UI checks run against your own test instance, never the owner's data directory.

## Before the first issue

1. `git checkout main && git pull`; the tree must be clean. `gh auth status` must succeed.
2. `gh label create needs-human --color D93F0B --description "Round stopped here; the owner decides" || true`
3. List the work: `gh issue list --milestone "<milestone>" --state open --json number,title,labels,body`.
   Skip issues labelled `needs-human` or `question`. Honour `--only`. Work in ascending
   issue number unless the milestone description gives an order.
4. For each issue read the full body and comments, and the ADR it concerns. If the issue is
   ambiguous in a way that two readings would produce materially different work, label it
   `needs-human` with the two readings and skip it; do not guess.
5. With `--dry-run`, print the plan (issue, ADR, files you expect to touch, whether it would be
   auto-merge eligible) and stop.

## Per issue

1. **Branch** from fresh `main`: `issue-<n>-<short-slug>`.
2. **Implement** with tests, the way the repo's notes say: service first, then page and API
   twin, template last. Add the `CHANGELOG.md` line under Unreleased with the issue number.
3. **Verify locally** before pushing: `uv run ruff check --fix . && uv run ruff format .`,
   `uv run mypy`, `uv run pytest`. If a template or `app.css` changed, run the relevant
   `tests/e2e` tests and look at the page in a browser at desktop and 320 px.
4. **Commit** with a conventional-commit subject and the `Co-Authored-By` trailer the session
   requires. **Push** and **open the PR ready for review** (not draft), body from
   `.github/PULL_REQUEST_TEMPLATE.md`: summary, the ADR, done-when, checklist,
   `Closes #<n>`, and the generated-with line. Opening it triggers Codex.
5. **Wait**: `uv run python scripts/pr_wait.py <pr>`. It returns one JSON line once every
   required check has finished and Codex has a `completed` state for the head commit.
   - `"ci": "fail"`: read the failed job with `gh run view <run> --job <job> --log-failed`,
     fix, push, and wait again. Counts as a fix round.
   - `"codex": {"findings": true, ...}`: `uv run python scripts/review_threads.py list <pr>`.
     Fix every P0, P1 and P2 in one commit. For a P3, fix it when it is a one-liner; otherwise
     reply with the reason and resolve. For a finding you believe is wrong, reply with the
     evidence (test name, line, ADR sentence) and `--keep-open`; the owner decides.
     Push, then for each fixed thread
     `uv run python scripts/review_threads.py resolve <pr> <thread-id> --reply "Fixed in <sha>: <what changed>"`,
     then comment `@codex review` on the PR and wait again. Counts as a fix round.
   - Timeout (exit 2): check once more by hand; if Codex never reviewed, comment
     `@codex review` once and wait again. A second timeout means `needs-human`.
   - After **two fix rounds** without a clean result: label `needs-human`, comment a summary
     of what was fixed and what remains, and go to the next issue.
6. **Clean** means: `"ci": "pass"`, `"codex": {"status": "completed", "for_head": true,
   "unresolved": false}`, and `review_threads.py list` prints `[]`.
   - If the PR is auto-merge eligible under the hard limits:
     `gh pr merge <pr> --auto --squash --delete-branch`, then
     `uv run python scripts/pr_wait.py <pr> --until-merged`. Protection re-checks everything.
   - Otherwise label `needs-human` and comment that it is green, reviewed and waiting for the
     owner's merge.
7. `git checkout main && git pull` before the next issue, so each branch starts from the
   merged state. If the previous PR is still waiting for the owner, branch from `main`
   anyway; do not stack.

## When everything is done

Print a summary for the owner, in this order, and stop:

- Merged: PR number, title, issue closed.
- Waiting for the owner: PR number, why (not eligible, or stopped after two fix rounds, or a
  disputed finding), and the link.
- Skipped issues and why.
- Anything you noticed that deserves its own issue, as one line each. Do not file them.

Keep working until the list is empty or every remaining issue is labelled. Do not stop
because the round is long.
