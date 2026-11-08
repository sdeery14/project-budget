# Security policy

ProjectBudget holds one owner's private budget data on an instance that may be reachable from the internet. Security issues are taken seriously even while the project is pre-release.

## Reporting a vulnerability

Please report privately. Do not open a public issue.

1. Preferred: [open a private security advisory](https://github.com/sdeery14/project-budget/security/advisories/new) on GitHub.
2. Fallback: email the maintainer at the address on the GitHub profile for [sdeery14](https://github.com/sdeery14).

Include what you found, how to reproduce it, and what you think the impact is. You will get an acknowledgement within 7 days and a fix or a decision within 90 days. Credit is given in the release notes unless you ask otherwise.

## Supported versions

The latest released minor version (0.1.x) receives security fixes. Upgrade with `pipx upgrade projectbudget`, by re-running `uvx projectbudget`, or by pulling the new container image.

## What V1 commits to

The threat model is in `docs/threat-model.md`. The V1 release gate (RFC v3, section 7) requires, among other things:

- No default credentials; the app refuses to serve until an owner is created.
- Argon2id password hashing, database-backed sessions, CSRF protection, login rate limiting.
- Every route except health, login and static assets requires the owner, verified by a test that walks the route table.
- API tokens stored hashed, shown once, revocable.
- Strict Content Security Policy with no inline scripts.
- Imports and manifest sync: safe YAML loading, size and time limits, GitHub hosts only.
- Secrets only from the environment; secret scanning in CI.
- No telemetry and no external calls except user-triggered GitHub sync.

A threat model document is written before any hosted offering (RFC v3, section 7, V1.x list).
