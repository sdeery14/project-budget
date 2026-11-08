# Release checklist

Versions come from git tags (hatch-vcs). Tagging `vX.Y.Z` on `main` runs `.github/workflows/release.yml`, which builds the wheel and sdist, publishes to PyPI through trusted publishing, pushes `ghcr.io/sdeery14/projectbudget:X.Y.Z` and `:latest`, and creates a GitHub Release with the matching `CHANGELOG.md` section as notes. Nothing is edited by hand for a release.

## One-time setup (first release only)

- [ ] PyPI: create the `projectbudget` project and add a trusted publisher for this repository, workflow `release.yml`, environment `pypi`.
- [ ] GitHub: create the `pypi` environment (optionally with a required reviewer); the GHCR package is created by the first push and should be set to public.
- [ ] Branch protection on `main` requires the five CI checks.

## Before tagging

- [ ] `main` is green: lint and types, tests on SQLite and PostgreSQL, container health, security scans (acceptance test 7).
- [ ] `CHANGELOG.md` has a `## [X.Y.Z] - YYYY-MM-DD` section; the Unreleased section is empty.
- [ ] `uv run pip-audit` is clean and Dependabot has nothing open that matters.
- [ ] Restore drill (see `docs/operations.md`): take a `projectbudget backup` and a `projectbudget export` of a real-looking workspace, restore each into a fresh database, confirm the backlog and timeline match.
- [ ] Fresh-clone check: on a machine without the repo, `uvx projectbudget init --username you` and `uvx projectbudget run` reach the login page; `docker run` of the previous image answers `/health` with no secrets set (acceptance test 8, first half).
- [ ] Someone unaffiliated follows `README.md` to a working install and reports what was unclear (acceptance test 8, second half). Fix the README before tagging, not after.
- [ ] `docs/threat-model.md` still describes the release; update the residual risks if anything changed.
- [ ] Accessibility pass: every form control has a label, the skip link works, every page is usable at 320 px width and with keyboard only.

## Tagging

```sh
git checkout main && git pull
git tag -a vX.Y.Z -m "ProjectBudget X.Y.Z"
git push origin vX.Y.Z
```

Watch the Release workflow. If it fails before publishing, fix forward and re-tag the next patch version; never move a tag.

## After the release

- [ ] `uvx projectbudget@X.Y.Z version` prints the version.
- [ ] `docker run --rm ghcr.io/sdeery14/projectbudget:X.Y.Z projectbudget version` prints the version.
- [ ] The GitHub Release shows the changelog notes and the two dist files.
- [ ] Update `SECURITY.md` "Supported versions" if the supported line changed.
- [ ] Open the next `## [Unreleased]` section in `CHANGELOG.md`.
