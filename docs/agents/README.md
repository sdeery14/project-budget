# Agent kit

Text to copy into a project repository so that a coding agent (Claude Code, Codex, or anything that reads `AGENTS.md`) can build and maintain that project's shopping list as a `project-budget.yaml`, the file ProjectBudget syncs from (ADR-013).

What is here:

| File | Copy it to | Read by |
| --- | --- | --- |
| [AGENTS-section.md](AGENTS-section.md) | append to the project's `AGENTS.md` (or `CLAUDE.md`) | Codex, Claude Code and any agent that reads `AGENTS.md`; paste it into ChatGPT once per conversation |
| [skills/shopping-list/SKILL.md](skills/shopping-list/SKILL.md) | `.claude/skills/shopping-list/SKILL.md` in the project | Claude Code, as `/shopping-list` |

Both say the same things: what the manifest is, where it lives, the rules the parser enforces, how to check the file with `uvx projectbudget manifest validate project-budget.yaml`, and how the owner brings it in. Neither installs or runs anything beyond that one read-only command.

For an agent that cannot reach your ProjectBudget server (ChatGPT, a cloud sandbox), hand it the export from the Data page as well: it is one JSON document with every project, item, price, stock level, purchase and open order, so the agent can plan against what you already have.

This kit matches ProjectBudget 0.1.x. A test in the ProjectBudget repository (`tests/test_agent_kit.py`) checks that every rule stated here is one the parser enforces, so when the manifest format changes the kit changes with it; re-copy it after upgrading. For live access, `projectbudget mcp` is an MCP server over the JSON API for agents on the same machine as the server; the configuration for Claude Code and Codex is in [docs/operations.md](../operations.md#mcp-server).
