# Vendored: Chart.js

- Version: 4.5.1 (pinned per ADR-002; upgrade deliberately, never by CDN)
- Source: https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.js
- SHA-256: ecc3cd1eeb8c34d2178e3f59fd63ec5a3d84358c11730af0b9958dc886d7652a
- License: MIT (see LICENSE.md in this directory)

Used only by the timeline page. The page passes the schedule to `static/timeline.js`
through `data-*` attributes on the canvas; there is no inline script, so the CSP
(`script-src 'self'`) holds. Chart.js 4 contains no `eval` or `new Function`.
