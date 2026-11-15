# ProjectBudget

@AGENTS.md

Notes for Claude Code sessions, beyond what AGENTS.md says:

- Browser checks run against a test instance of your own, with its own data directory and
  owner. Never run them against the owner's instance or data.
- Look at any changed page in a real browser at desktop and 320 px before pushing; the
  browser tests under `tests/e2e` exist because unit tests missed layout and flow problems.
- `/round <milestone>` works a milestone unattended: one PR per issue, CI and Codex review,
  fixes, auto-merge where allowed, `needs-human` where not. The procedure and its hard
  limits are in `.claude/skills/round/SKILL.md`.
- The owner approves every release tag explicitly. Nothing is tagged or published otherwise.
