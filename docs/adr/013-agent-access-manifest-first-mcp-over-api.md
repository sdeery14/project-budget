# ADR-013: Agent access: manifest first, then an MCP server over the API

**Status:** Accepted
**Date:** 2026-11-17
**Implements:** RFC v3 §2 (per-project lists), §6 (sync), §7 (security); adds one interface to ADR-002 and ADR-005; amends the "Not in V1" list of RFC v3 §2 by naming what stays deferred

## Context

The owner plans projects with coding agents: Claude Code and Codex CLI in the project's own repository, ChatGPT and Codex cloud for brainstorming away from the machine. Those agents should be able to build a project's shopping list, read what the workspace already knows (prices, stock, open orders, the queue and the month it affords each item) and record what was bought, without the owner translating between them and the app.

Facts that constrain the design:

- A project's shopping list already has a native, versioned form: `project-budget.yaml` next to the project, validated by `docs/manifest.schema.json`, pulled in by sync with a plan shown before apply (ADR-007). The manifest owns name, quantity, priority and order; the owner owns intent, sharing, notes and the catalog mapping; a manifest can never touch purchases, stock or the budget.
- Every page has a JSON twin under `/api/v1` (ADR-002), authenticated by bearer tokens the owner issues on the tokens page, read-only or read-write, shown once and stored hashed (ADR-005). The threat model treats "API client ↔ app" as a boundary whose trusted side is such a token.
- Agents on the owner's machine can write files, run commands and reach `localhost`. ChatGPT and Codex cloud cannot reach a server on a laptop or NAS; they work with text and files the owner gives them, or with a remote MCP server over HTTPS.
- The deferred list (RFC v3 §2, binding under ADR-011) includes GitHub OAuth or App, webhooks, multi-user, token scopes beyond read/write and a hosted instance. The threat model lists scoped tokens, an audit log and a hosted instance as out of scope for V1.
- One Python process, no background workers, no in-process state that outlives a request (RFC v3 §3).

A new interface for agents has to add capability without adding a second authorization path, a second data model, or anything that would quietly move a deferred item earlier.

## Decision

1. **The manifest is the agent's way to build a shopping list.** An agent working in a project repository writes or edits `project-budget.yaml`; the owner (or the agent, through the API) previews and applies it through sync like any other change. No agent-specific import format is added. To make that reliable without a running server, the CLI gains `projectbudget manifest validate PATH`, which parses the file with the same parser sync uses and prints the same line-level errors, exit code 1 on any error, 0 otherwise. It reads the file only; it opens no database.

2. **The export document is the agent's way to read the workspace offline.** For agents that cannot reach the server, the owner hands them the export from the Data page (ADR-008). No second, smaller export is added; a reader takes what it needs from the one document.

3. **An agent kit ships with the app as documentation to copy into a project repository**, under `docs/agents/`: an `AGENTS.md` section and a Claude Code skill. Both say what a manifest is, link the schema, state the rules the parser enforces, name the validate command, and say how to hand the export to an offline agent. The kit is plain text; it installs nothing and runs nothing.

4. **Live access is an MCP server over the existing API.** `projectbudget mcp` starts a Model Context Protocol server on stdio, in the same package, that calls `/api/v1` on a configured base URL with an API token from the environment (`PROJECTBUDGET_API_TOKEN`, never an argument). It exposes a small, named tool set that mirrors page actions: list projects, get a project, read the backlog, read the timeline, get a catalog item, add or update a requirement, record a purchase, receive a line, consume against a requirement, preview and apply a sync source. Each tool calls one API endpoint and returns its JSON; the server holds no state and contains no business logic (the ADR-002 rule for routers applies to it). A read-only token yields a read-only server, because the API already refuses writes with 403; the server does not add scopes.

5. **What stays deferred.** Serving the MCP endpoint over HTTP or exposing it to the internet, OAuth for agents, per-project or per-tool token scopes, an audit log of agent actions, and any agent-initiated change to the budget plan beyond what the API already allows. Each is a V1.x item in RFC v3 §7 and needs its own ADR. Token management stays browser-only: no MCP tool creates or revokes tokens.

## Consequences

Easier: an agent in a project repository has one file to write and one command to check it; the owner reviews the sync plan, which is already the designed control point for manifest content (threat model, "Malicious manifest"). Local agents get live read and write access with the same token the API already understands, so revocation, read-only mode and the single-owner principal all keep working unchanged. The tool set is a thin map of existing endpoints, so a page, its JSON twin and its MCP tool cannot drift apart without a test noticing.

Harder: the MCP server is a third client of the API and must be kept in step with `api/schemas.py`; a test that walks the tool list against the OpenAPI document is the price. ChatGPT gets no live access until a later ADR decides on remote exposure; until then it works from the export and the manifest, which is a deliberate limit, not an oversight. The agent kit lives in two places (this repo and each project repo that copies it), so it needs a version line and a note on where the current copy is.

Out of scope, restated: remote MCP, OAuth, scopes, audit log, token management through agents. Nothing on the "Not in V1" list moves earlier; this ADR adds a client of the existing API and a read-only CLI command.

Sequencing: items 1 to 3 are a documentation-and-CLI milestone with no interface change and can proceed on this ADR's acceptance; item 4 is its own PR, named against this ADR, with the tool list in `docs/operations.md` and the new client added to the threat model's trust boundaries table.
