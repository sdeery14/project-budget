# Vendored: htmx

- Version: 2.0.11 (pinned per ADR-002; upgrade deliberately, never by CDN)
- Source: https://unpkg.com/htmx.org@2.0.11/dist/htmx.min.js
- SHA-256: d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717
- License: Zero-Clause BSD (see LICENSE in this directory)

Templates must not use `hx-on` attributes or `js:`-prefixed values; the CSP has
no `unsafe-eval` and `allowEval` is off in the htmx config meta tag. A test greps for both.
