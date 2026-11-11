# Threat model (V1 draft)

**Status:** draft written for Milestone 5 (RFC v3 §7). It is deliberately two pages: what the single-owner V1 protects, who it protects against, and what it does not try to do. It is expanded before any hosted or multi-user offering.

## What is being protected

One owner's private budget data: projects, requirements, prices, stock, purchases, the budget plan and sync configuration, held in one SQLite file or PostgreSQL database on a laptop, NAS or small VPS that may be reachable from the internet. There is one account. There is no payment data, no bank connection and no third-party identity.

## Trust boundaries

| Boundary | Trusted side | Untrusted side |
| --- | --- | --- |
| Browser ↔ app | the owner's authenticated session | anyone who can reach the port, other sites in the same browser |
| API client ↔ app | a bearer token the owner issued | anyone holding a leaked token |
| App ↔ sync sources | the owner's import directory and `api.github.com` | manifest content, which may be written by someone else |
| App ↔ database | the database user and file | other local users of the host |
| Operator ↔ host | the person who runs `init` and keeps backups | everyone else |

## Threats and what V1 does about them

**Credential leakage.** Passwords are hashed with Argon2id; the session cookie holds a random secret and the database stores only its SHA-256; API tokens are shown once and stored hashed. A leaked token cannot mint tokens (token management needs a browser session) and can be revoked from the UI. Login is rate-limited per username and per address with database-backed buckets and constant-time comparison; unknown usernames cost the same work as known ones. Residual risk: a token pasted into a script on a shared machine; mitigation is read-only tokens and revocation.

**Session theft and cross-site requests.** Cookies are HttpOnly, SameSite=Lax and Secure by default; every state-changing request needs a same-origin `Origin` or `Referer` header and, for cookie sessions, the per-session CSRF token. Sessions have an idle timeout and a 30-day maximum and are rotated at login. Residual risk: an operator who turns Secure cookies off on an untrusted network.

**Cross-user exposure.** Not applicable in V1: there is exactly one owner, and every route except `/health`, `/login` and static assets requires that owner (a test walks the route table). The `Principal` passed to every service function is the chokepoint where workspace isolation will be enforced before multi-user exists (ADR-005).

**Malicious manifest.** YAML is loaded with the safe loader only; manifests are capped at 2 MB; the schema rejects unknown fields, floats where money belongs and duplicate ids; names never identify anything, so a crafted name cannot hijack another item. A manifest can only ever create or edit requirements, unreviewed catalog items it created and manifest-origin estimates; it cannot touch purchases, stock or the budget, and a disappearing item archives rather than deletes. Kind and unit changes on items the source does not own are rejected per item. Residual risk: a manifest that floods the catalog with junk items; mitigation is the dry-run plan shown before apply and detach, which keeps everything and drops the source.

**Server-side request forgery through sync.** There is no URL input. File and CSV sources resolve under the configured import directory and refuse absolute paths, drive letters and `..`. GitHub sources are `owner/repo` plus branch and path; the client speaks HTTPS to `api.github.com` through an opener with no file, FTP, data or redirect handlers. Tokens for private repositories are read at fetch time from `PROJECTBUDGET_GITHUB_TOKEN` or a per-source named environment variable, and never stored.

**Webhook forgery.** Not applicable: there are no webhooks or inbound integrations in V1 (ADR-007 defers them).

**Injection and content attacks.** All database access goes through SQLAlchemy parameters. Templates are auto-escaped; the Content Security Policy allows no inline scripts or styles and no remote sources, htmx runs with `allowEval` off and `selfRequestsOnly` on, and a test greps every template for `hx-on`, `js:`, inline `<script>` and `style=`. Uploaded manifests are parsed, never rendered. Residual risk: a rendered error message echoing manifest text; it is escaped like any other value.

**Compromised dependency.** Dependencies are locked (`uv.lock`), audited with `pip-audit` on every CI run, updated by Dependabot and scanned by bandit; htmx and Chart.js are vendored with documented checksums rather than loaded from a CDN. Residual risk: a malicious release of a direct dependency between audits; mitigation is the lock file and reviewing Dependabot changes.

**Backup loss and data loss.** `projectbudget backup` writes a consistent SQLite copy with `VACUUM INTO` (a plain file copy under WAL is unsafe); `projectbudget export` writes the whole workspace as one JSON document that imports again id for id; PostgreSQL users take `pg_dump`. The release checklist includes a restore drill. Residual risk: backups stored on the same disk; the operations guide says to copy them elsewhere.

**Local host compromise.** The SQLite file and its directory are created with owner-only permissions; the container runs as a non-root user with one volume. Anyone with read access to the host's data directory has the data; that is outside what the application can defend.

**Denial of service.** Request bodies for manifests and imports are capped (2 MB and 50 MB); the login rate limit bounds password guessing; one worker serves one owner. The app is not designed to withstand a volumetric attack; put it behind a reverse proxy if that matters.

## Explicitly out of scope for V1

Multi-user isolation, OAuth or GitHub App integration, webhooks, scoped tokens, an audit log, encrypted at-rest token storage, and a hosted instance. Each is listed under V1.x in RFC v3 §7 and needs a new ADR (ADR-011).

## Review cadence

Revisit this document when any item above moves out of scope, when a security report arrives (see `SECURITY.md`), and before the first hosted deployment.
