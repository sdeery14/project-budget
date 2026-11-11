"""The sync service: sources, preview, apply, no-op, rejects, archive and reappear, ownership, runs."""

import contextlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from projectbudget.adapters.sources import FetchedManifest, GitHubNotFoundError, SourceFetchError
from projectbudget.domain.catalog import (
    REMOVED_FROM_SOURCE,
    EstimateOrigin,
    Intent,
    ItemKind,
    ReviewState,
    RunStatus,
    SourceKind,
)
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import (
    backlog,
    catalog,
    inventory,
    projects,
    purchases,
    requirements,
    sync,
)
from projectbudget.services.guards import ReadOnlyPrincipalError
from projectbudget.services.sync import SourceInfo

NOW = datetime(2026, 11, 8, 9, 0, 0)
LATER = datetime(
    2026, 11, 8, 9, 5, 0
)  # runs are listed newest first; equal timestamps have no order
AS_OF = date(2026, 11, 1)

MANIFEST_V1 = """version: 1
project:
  id: diesel-heater
  name: Diesel heater install
items:
  - id: fuel-line
    name: 6 mm fuel line
    qty: 3
    unit: m
    estimate: { amount: "12.50", currency: USD }
  - id: clamps
    name: Hose clamp
    qty: 6
    kind: reusable
    priority: 1
"""

MANIFEST_V2 = """version: 1
project:
  id: diesel-heater
  name: Diesel heater install
items:
  - id: fuel-line
    name: 6 mm fuel line
    qty: 5
    unit: m
    estimate: { amount: "13.00", currency: USD }
  - id: exhaust
    name: Exhaust pipe
    qty: 1
"""


def fetched(text: str, *, as_of: date = AS_OF, revision: str | None = None) -> FetchedManifest:
    return FetchedManifest.of(
        text.encode("utf-8"), revision=revision, as_of=as_of, description="test"
    )


@pytest.fixture
def import_dir(tmp_path: Path) -> Path:
    (tmp_path / "heater").mkdir()
    target = tmp_path / "heater" / "project-budget.yaml"
    target.write_text(MANIFEST_V1, encoding="utf-8")
    # The file's mtime is the estimate date for file sources; pin it so tests are deterministic.
    stamp = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 12, tzinfo=UTC).timestamp()
    os.utime(target, (stamp, stamp))
    return tmp_path


@pytest.fixture
def source(session_factory: SessionFactory, principal: Principal, import_dir: Path) -> SourceInfo:
    return sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(kind=SourceKind.FILE, locator="heater/project-budget.yaml"),
        import_dir=import_dir,
        now=NOW,
    )


def test_create_source_validates_per_kind(
    session_factory: SessionFactory, principal: Principal, import_dir: Path
) -> None:
    with pytest.raises(sync.InvalidSourceError, match=r"outside|climb"):
        sync.create_source(
            session_factory,
            principal,
            sync.SourceCreate(SourceKind.FILE, "../x.yaml"),
            import_dir=import_dir,
        )
    with pytest.raises(sync.InvalidSourceError, match="external project id"):
        sync.create_source(
            session_factory,
            principal,
            sync.SourceCreate(SourceKind.CSV, "heater/items.csv"),
            import_dir=import_dir,
        )
    with pytest.raises(sync.InvalidSourceError, match="Only GitHub"):
        sync.create_source(
            session_factory,
            principal,
            sync.SourceCreate(SourceKind.FILE, "heater/project-budget.yaml", token_env="X"),
            import_dir=import_dir,
        )
    with pytest.raises(sync.InvalidSourceError, match="owner/repo"):
        sync.create_source(
            session_factory,
            principal,
            sync.SourceCreate(SourceKind.GITHUB, "nope"),
            import_dir=import_dir,
        )
    with pytest.raises(sync.InvalidSourceError, match="NAME"):
        sync.create_source(
            session_factory,
            principal,
            sync.SourceCreate(SourceKind.GITHUB, "o/r", token_env="ghp_actual_token_value"),
            import_dir=import_dir,
        )
    github = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.GITHUB, " o/r ", branch="main", token_env="GITHUB_TOKEN"),
        import_dir=import_dir,
        now=NOW,
    )
    assert (github.locator, github.branch, github.path, github.token_env) == (
        "o/r",
        "main",
        "project-budget.yaml",
        "GITHUB_TOKEN",
    )
    assert github.label == "o/r@main:project-budget.yaml"
    csv = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.CSV, "heater/items.csv", external_project_id="garden-gate"),
        import_dir=import_dir,
        now=LATER,  # sources list in creation order; equal timestamps have no order
    )
    assert csv.external_project_id == "garden-gate"
    assert [s.id for s in sync.list_sources(session_factory, principal)] == [github.id, csv.id]
    with pytest.raises(sync.SourceNotFoundError):
        sync.get_source(session_factory, principal, "nope")


def test_first_apply_creates_project_items_requirements_and_estimates(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    shown = sync.preview(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    assert not shown.noop
    assert shown.plan is not None
    assert shown.plan.counts["creates"] == 2
    assert shown.fetched.description == "file heater/project-budget.yaml"

    result = sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    assert result.run.status is RunStatus.APPLIED
    assert result.run.summary["creates"] == 2
    assert result.source.project_name == "Diesel heater install"
    assert result.source.last_content_hash == shown.fetched.content_hash

    project = projects.get_project(session_factory, principal, result.source.project_id or "")
    assert project.name == "Diesel heater install"
    reqs = requirements.list_requirements(session_factory, principal, project.id)
    assert [(r.qty, r.priority) for r in reqs] == [(3, 2), (6, 1)]
    assert [r.sort_order for r in reqs] == [0, 1]  # the file's order (#34)
    fuel = catalog.get_item_by_slug(session_factory, principal, "6-mm-fuel-line")
    assert fuel is not None
    assert (fuel.unit, fuel.kind, fuel.review_state) == (
        "m",
        ItemKind.CONSUMABLE,
        ReviewState.UNREVIEWED,
    )
    estimate = catalog.current_estimate(session_factory, principal, fuel.id)
    assert estimate is not None
    assert (estimate.price, estimate.as_of, estimate.origin) == (
        Money(1250, "USD"),
        AS_OF,
        EstimateOrigin.MANIFEST,
    )
    clamp = catalog.get_item_by_slug(session_factory, principal, "hose-clamp")
    assert clamp is not None
    assert clamp.kind is ItemKind.REUSABLE


def test_same_content_is_a_noop_and_a_forced_rerun_has_an_empty_plan(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    again = sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=LATER)
    assert again.noop
    assert again.run.status is RunStatus.NOOP
    forced = sync.preview(
        session_factory, principal, source.id, import_dir=import_dir, force=True, now=NOW
    )
    assert forced.plan is not None
    assert forced.plan.empty
    assert forced.plan.counts["unchanged"] == 2
    statuses = [r.status for r in sync.list_runs(session_factory, principal, source.id)]
    assert statuses == [RunStatus.NOOP, RunStatus.APPLIED]


def test_changed_manifest_updates_archives_creates_and_records_a_new_estimate(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    first = sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    project_id = first.source.project_id or ""
    second = sync.apply(
        session_factory,
        principal,
        source.id,
        import_dir=import_dir,
        fetched=fetched(MANIFEST_V2, as_of=date(2026, 11, 6)),
        now=NOW,
    )
    assert second.plan is not None
    assert second.plan.counts == {
        "creates": 1,
        "adoptions": 0,
        "updates": 1,
        "reactivations": 0,
        "archives": 1,
        "rejects": 0,
        "estimate_rejects": 0,
        "unchanged": 0,
    }
    reqs = {
        r.catalog_item_id: r
        for r in requirements.list_requirements(
            session_factory, principal, project_id, include_inactive=True
        )
    }
    fuel = catalog.get_item_by_slug(session_factory, principal, "6-mm-fuel-line")
    clamp = catalog.get_item_by_slug(session_factory, principal, "hose-clamp")
    assert fuel is not None
    assert clamp is not None
    assert reqs[fuel.id].qty == 5
    assert (reqs[clamp.id].intent, reqs[clamp.id].archive_reason) == (
        Intent.ARCHIVED,
        REMOVED_FROM_SOURCE,
    )
    estimate = catalog.current_estimate(session_factory, principal, fuel.id)
    assert estimate is not None
    assert (estimate.price, estimate.as_of) == (Money(1300, "USD"), date(2026, 11, 6))
    assert len(catalog.list_estimates(session_factory, principal, fuel.id)) == 2

    # The clamps come back: the same requirement is reactivated.
    third = sync.apply(
        session_factory, principal, source.id, import_dir=import_dir, force=True, now=NOW
    )
    assert third.plan is not None
    assert third.plan.counts["reactivations"] == 1
    assert third.plan.counts["archives"] == 1  # the exhaust pipe is gone again
    back = requirements.get_requirement(session_factory, principal, reqs[clamp.id].id)
    assert (back.intent, back.archive_reason) == (Intent.ACTIVE, None)


def test_reordering_the_file_reorders_the_requirements_and_the_backlog(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    """ADR-012 (#34): the manifest's order is the purchase order, re-set on every apply."""
    fuel_line = [
        "  - id: fuel-line",
        "    name: 6 mm fuel line",
        "    qty: 3",
        "    unit: m",
        '    estimate: { amount: "12.50", currency: USD }',
    ]
    clamps = [
        "  - id: clamps",
        "    name: Hose clamp",
        "    qty: 6",
        "    kind: reusable",
        '    estimate: { amount: "1.00", currency: USD }',
    ]
    head = [
        "version: 1",
        "project:",
        "  id: diesel-heater",
        "  name: Diesel heater install",
        "items:",
    ]
    fuel_first = "\n".join([*head, *fuel_line, *clamps, ""])
    clamps_first = "\n".join([*head, *clamps, *fuel_line, ""])

    first = sync.apply(
        session_factory, principal, source.id, import_dir=import_dir, fetched=fetched(fuel_first)
    )
    project_id = first.source.project_id or ""
    names_before = [row.item.name for row in backlog.get_backlog(session_factory, principal).rows]
    assert names_before == ["6 mm fuel line", "Hose clamp"]  # both P2: the file's order decides

    result = sync.apply(
        session_factory, principal, source.id, import_dir=import_dir, fetched=fetched(clamps_first)
    )
    assert result.plan is not None
    assert [(u.external_id, [c.field for c in u.changes]) for u in result.plan.updates] == [
        ("clamps", ["sort_order"]),
        ("fuel-line", ["sort_order"]),
    ]
    reqs = requirements.list_requirements(session_factory, principal, project_id)
    assert [(r.qty, r.sort_order) for r in reqs] == [(6, 0), (3, 1)]
    names_after = [row.item.name for row in backlog.get_backlog(session_factory, principal).rows]
    assert names_after == ["Hose clamp", "6 mm fuel line"]


def test_attaching_a_source_to_a_hand_built_project_adopts_matching_requirements(
    session_factory: SessionFactory, principal: Principal, import_dir: Path
) -> None:
    """ADR-012 (#26): one project, not two; adopted rows keep what the owner set by hand."""
    project = projects.create_project(
        session_factory, principal, projects.ProjectCreate(name="Heater (by hand)"), now=NOW
    )
    clamp = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(name="Hose clamp", kind=ItemKind.REUSABLE),
    )
    clamp_req = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(
            project_id=project.id, qty=2, priority=3, catalog_item_id=clamp.id, notes="stainless"
        ),
    )
    requirements.set_intent(session_factory, principal, clamp_req.id, Intent.PAUSED)
    tape_req = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=1, item_text="Tape"),
    )

    source = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(
            kind=SourceKind.FILE, locator="heater/project-budget.yaml", project_id=project.id
        ),
        import_dir=import_dir,
        now=NOW,
    )
    assert (source.project_id, source.project_name) == (project.id, "Heater (by hand)")

    shown = sync.preview(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    assert shown.plan is not None
    assert [c.external_id for c in shown.plan.creates] == ["fuel-line"]
    (adopt,) = shown.plan.adoptions
    assert (adopt.requirement_id, adopt.external_id, adopt.item_name) == (
        clamp_req.id,
        "clamps",
        "Hose clamp",
    )
    assert [(c.field, c.old, c.new) for c in adopt.changes] == [
        ("qty", 2, 6),
        ("priority", 3, 1),
        ("sort_order", 0, 1),
    ]
    assert shown.plan.warnings == ()

    result = sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    assert result.run.summary["adoptions"] == 1
    assert [p.name for p in projects.list_projects(session_factory, principal)] == [
        "Diesel heater install"  # the manifest owns the project name once attached
    ]
    adopted = requirements.get_requirement(session_factory, principal, clamp_req.id)
    assert (adopted.qty, adopted.priority, adopted.sort_order) == (6, 1, 1)
    assert (adopted.intent, adopted.notes) == (Intent.PAUSED, "stainless")  # kept
    tape = requirements.get_requirement(session_factory, principal, tape_req.id)
    assert (tape.qty, tape.priority) == (1, 2)  # untouched, still manual
    reqs = requirements.list_requirements(
        session_factory, principal, project.id, include_inactive=True
    )
    assert len(reqs) == 3  # clamp (adopted), tape (manual), fuel line (created)

    # The second apply treats the adopted row as the source's own: no adoption, no duplicate.
    again = sync.preview(
        session_factory, principal, source.id, import_dir=import_dir, force=True, now=LATER
    )
    assert again.plan is not None
    assert (again.plan.adoptions, again.plan.creates) == ((), ())
    assert again.plan.counts["unchanged"] == 2

    with pytest.raises(sync.InvalidSourceError, match="already synced"):
        sync.create_source(
            session_factory,
            principal,
            sync.SourceCreate(
                kind=SourceKind.FILE, locator="heater/project-budget.yaml", project_id=project.id
            ),
            import_dir=import_dir,
        )


def test_two_concurrent_attachments_to_one_project_cannot_both_succeed(
    session_factory: SessionFactory,
    principal: Principal,
    import_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The project row is locked before its attachment is checked (review finding on #44)."""
    project = projects.create_project(
        session_factory, principal, projects.ProjectCreate(name="Heater"), now=NOW
    )
    barrier = threading.Barrier(2)
    real_lock = sync._lock_project

    def paused(db: object, project_id: str) -> object:
        # Both requests arrive at the lock together; the loser must then see the winner's write.
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=1.5)
        return real_lock(db, project_id)  # type: ignore[arg-type]

    monkeypatch.setattr(sync, "_lock_project", paused)

    def attempt(_: int) -> str:
        try:
            sync.create_source(
                session_factory,
                principal,
                sync.SourceCreate(
                    kind=SourceKind.FILE,
                    locator="heater/project-budget.yaml",
                    project_id=project.id,
                ),
                import_dir=import_dir,
            )
        except sync.InvalidSourceError as exc:
            return "refused" if "already synced" in str(exc) else f"other: {exc}"
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(attempt, range(2)))
    assert results == ["ok", "refused"]
    attached = [s for s in sync.list_sources(session_factory, principal) if s.project_id]
    assert len(attached) == 1
    assert projects.get_project(session_factory, principal, project.id).source_id == attached[0].id


def test_an_unattached_source_warns_about_a_project_with_the_same_name(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    projects.create_project(
        session_factory, principal, projects.ProjectCreate(name="diesel heater INSTALL"), now=NOW
    )
    shown = sync.preview(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    assert shown.plan is not None
    assert len(shown.plan.warnings) == 1
    assert "'diesel heater INSTALL' already exists" in shown.plan.warnings[0]
    sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    names = sorted(p.name for p in projects.list_projects(session_factory, principal))
    assert names == ["Diesel heater install", "diesel heater INSTALL"]  # still creates, as warned


def test_schema_error_rejects_the_file_and_records_a_rejected_run(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    bad = fetched(
        "version: 1\nproject: {id: x}\nitems:\n  - {id: a, name: A, qty: 1, estimate: {amount: 1.5}}\n"
    )
    with pytest.raises(sync.ManifestRejectedError) as excinfo:
        sync.preview(
            session_factory, principal, source.id, import_dir=import_dir, fetched=bad, now=NOW
        )
    assert "quoted decimal string" in str(excinfo.value)
    runs = sync.list_runs(session_factory, principal, source.id)
    assert runs[0].status is RunStatus.REJECTED
    assert runs[0].id == excinfo.value.run_id
    assert "issues" in runs[0].summary
    assert projects.list_projects(session_factory, principal) == []


def test_ownership_conflict_rejects_the_item_and_applies_the_rest(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    confirmed = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(
            name="6 mm fuel line", unit="ft", review_state=ReviewState.CONFIRMED
        ),
    )
    result = sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    assert result.plan is not None
    assert [r.external_id for r in result.plan.rejects] == ["fuel-line"]
    assert "does not own" in result.plan.rejects[0].reason
    assert result.plan.counts["creates"] == 1
    assert catalog.get_item(session_factory, principal, confirmed.id).unit == "ft"
    project_id = result.source.project_id or ""
    assert len(requirements.list_requirements(session_factory, principal, project_id)) == 1


def test_estimate_currency_mismatch_applies_the_item_without_the_estimate(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    text = MANIFEST_V1.replace("currency: USD", "currency: EUR")
    result = sync.apply(
        session_factory, principal, source.id, import_dir=import_dir, fetched=fetched(text), now=NOW
    )
    assert result.plan is not None
    assert [r.external_id for r in result.plan.estimate_rejects] == ["fuel-line"]
    fuel = catalog.get_item_by_slug(session_factory, principal, "6-mm-fuel-line")
    assert fuel is not None
    assert catalog.current_estimate(session_factory, principal, fuel.id) is None


def test_source_owned_item_follows_kind_and_unit_until_it_is_referenced_elsewhere(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    changed = MANIFEST_V1.replace("unit: m\n", "unit: ft\n")
    result = sync.apply(
        session_factory,
        principal,
        source.id,
        import_dir=import_dir,
        fetched=fetched(changed),
        now=NOW,
    )
    assert result.plan is not None
    fuel = catalog.get_item_by_slug(session_factory, principal, "6-mm-fuel-line")
    assert fuel is not None
    assert fuel.unit == "ft"
    # Stock arrives: the item is now referenced elsewhere and the manifest may no longer change its unit.
    inventory.adjust(session_factory, principal, fuel.id, 2, note="found")
    back = sync.apply(
        session_factory,
        principal,
        source.id,
        import_dir=import_dir,
        fetched=fetched(MANIFEST_V1),
        now=NOW,
    )
    assert back.plan is not None
    assert [r.external_id for r in back.plan.rejects] == ["fuel-line"]
    assert catalog.get_item(session_factory, principal, fuel.id).unit == "ft"


def test_apply_refuses_when_the_source_changed_since_the_preview(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    shown = sync.preview(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    (import_dir / "heater" / "project-budget.yaml").write_text(MANIFEST_V2, encoding="utf-8")
    with pytest.raises(sync.SourceChangedError):
        sync.apply(
            session_factory,
            principal,
            source.id,
            import_dir=import_dir,
            expected_content_hash=shown.fetched.content_hash,
            now=NOW,
        )
    assert projects.list_projects(session_factory, principal) == []


def test_missing_file_and_missing_token_variable(
    session_factory: SessionFactory,
    principal: Principal,
    source: SourceInfo,
    import_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (import_dir / "heater" / "project-budget.yaml").unlink()
    with pytest.raises(SourceFetchError, match="No file"):
        sync.preview(session_factory, principal, source.id, import_dir=import_dir)
    github = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.GITHUB, "o/r", token_env="PB_TEST_TOKEN"),
        import_dir=import_dir,
    )
    monkeypatch.delenv("PB_TEST_TOKEN", raising=False)
    with pytest.raises(SourceFetchError, match="PB_TEST_TOKEN"):
        sync.preview(session_factory, principal, github.id, import_dir=import_dir)


class RecordingGitHub:
    """Stands in for GitHubClient: records the token each fetch used, or answers 404."""

    def __init__(self, *, not_found: bool = False) -> None:
        self.not_found = not_found
        self.tokens: list[str | None] = []

    def fetch(
        self, locator: str, *, branch: str | None, path: str | None, token: str | None
    ) -> FetchedManifest:
        self.tokens.append(token)
        if self.not_found:
            raise GitHubNotFoundError(f"GitHub answered 404 for /repos/{locator}/commits")
        return fetched(MANIFEST_V1, revision="abc123")


def test_github_sources_use_the_server_default_token_unless_they_name_their_own(
    session_factory: SessionFactory,
    principal: Principal,
    import_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#30: one PROJECTBUDGET_GITHUB_TOKEN for every source; a per-source variable overrides it."""
    plain = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.GITHUB, "o/r"),
        import_dir=import_dir,
    )
    own = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.GITHUB, "other/repo", token_env="PB_OTHER_TOKEN"),
        import_dir=import_dir,
    )
    monkeypatch.setenv("PB_OTHER_TOKEN", "token-for-other")
    github = RecordingGitHub()
    sync.preview(
        session_factory,
        principal,
        plain.id,
        import_dir=import_dir,
        github=github,  # type: ignore[arg-type]
        github_token="server-default",
    )
    sync.preview(
        session_factory,
        principal,
        own.id,
        import_dir=import_dir,
        github=github,  # type: ignore[arg-type]
        github_token="server-default",
    )
    sync.preview(
        session_factory,
        principal,
        plain.id,
        import_dir=import_dir,
        github=github,  # type: ignore[arg-type]
    )
    assert github.tokens == ["server-default", "token-for-other", None]


def test_a_github_404_explains_the_missing_or_insufficient_token(
    session_factory: SessionFactory, principal: Principal, import_dir: Path
) -> None:
    """#29: GitHub answers 404 for a private repository without a token; say so."""
    source = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.GITHUB, "o/r"),
        import_dir=import_dir,
    )
    github = RecordingGitHub(not_found=True)
    with pytest.raises(SourceFetchError) as without:
        sync.preview(
            session_factory,
            principal,
            source.id,
            import_dir=import_dir,
            github=github,  # type: ignore[arg-type]
        )
    message = str(without.value)
    assert message.startswith("GitHub answered 404 for /repos/o/r/commits")
    assert "private repository as not found" in message
    assert "PROJECTBUDGET_GITHUB_TOKEN" in message
    assert "docs/operations.md" in message

    with pytest.raises(SourceFetchError) as with_token:
        sync.preview(
            session_factory,
            principal,
            source.id,
            import_dir=import_dir,
            github=github,  # type: ignore[arg-type]
            github_token="server-default",
        )
    assert "does not cover this repository" in str(with_token.value)
    assert "server-default" not in str(with_token.value)


def test_source_settings_can_be_changed_in_place(
    session_factory: SessionFactory, principal: Principal, import_dir: Path
) -> None:
    """#29: branch, path and token variable are editable; kind and locator are not."""
    source = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.GITHUB, "o/r"),
        import_dir=import_dir,
    )
    changed = sync.update_source(
        session_factory,
        principal,
        source.id,
        sync.SourceUpdate(branch="main", path="budget/project-budget.yaml", token_env="MY_TOKEN"),
        import_dir=import_dir,
        now=LATER,
    )
    assert (changed.branch, changed.path, changed.token_env) == (
        "main",
        "budget/project-budget.yaml",
        "MY_TOKEN",
    )
    assert (changed.kind, changed.locator, changed.updated_at) == (SourceKind.GITHUB, "o/r", LATER)
    assert sync.get_source(session_factory, principal, source.id) == changed

    cleared = sync.update_source(
        session_factory, principal, source.id, sync.SourceUpdate(), import_dir=import_dir
    )
    assert (cleared.branch, cleared.path, cleared.token_env) == (None, "project-budget.yaml", None)

    with pytest.raises(sync.InvalidSourceError, match="NAME"):
        sync.update_source(
            session_factory,
            principal,
            source.id,
            sync.SourceUpdate(token_env="ghp_pasted_the_token"),
            import_dir=import_dir,
        )
    with pytest.raises(sync.InvalidSourceError, match="Branch"):
        sync.update_source(
            session_factory,
            principal,
            source.id,
            sync.SourceUpdate(branch="bad branch"),
            import_dir=import_dir,
        )
    assert (
        sync.get_source(session_factory, principal, source.id) == cleared
    )  # refused edits change nothing

    csv = sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.CSV, "heater/items.csv", external_project_id="garden-gate"),
        import_dir=import_dir,
    )
    with pytest.raises(sync.InvalidSourceError, match="external project id"):
        sync.update_source(
            session_factory, principal, csv.id, sync.SourceUpdate(), import_dir=import_dir
        )
    with pytest.raises(sync.InvalidSourceError, match="Only GitHub"):
        sync.update_source(
            session_factory,
            principal,
            csv.id,
            sync.SourceUpdate(external_project_id="garden-gate", token_env="X"),
            import_dir=import_dir,
        )
    renamed = sync.update_source(
        session_factory,
        principal,
        csv.id,
        sync.SourceUpdate(external_project_id="side-gate"),
        import_dir=import_dir,
    )
    assert renamed.external_project_id == "side-gate"


def test_detach_keeps_the_project_and_requirements_as_manual(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    result = sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    project_id = result.source.project_id or ""
    sync.detach_source(session_factory, principal, source.id)
    assert sync.list_sources(session_factory, principal) == []
    with pytest.raises(sync.SourceNotFoundError):
        sync.list_runs(session_factory, principal, source.id)
    assert (
        projects.get_project(session_factory, principal, project_id).name == "Diesel heater install"
    )
    assert len(requirements.list_requirements(session_factory, principal, project_id)) == 2


def test_sync_never_touches_purchases_stock_or_budget(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    first = sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    clamp = catalog.get_item_by_slug(session_factory, principal, "hose-clamp")
    assert clamp is not None
    purchases.mark_purchased(session_factory, principal, clamp.id, 2, Money(300, "USD"))
    before_stock = inventory.on_hand(session_factory, principal, clamp.id)
    sync.apply(
        session_factory,
        principal,
        source.id,
        import_dir=import_dir,
        fetched=fetched(MANIFEST_V2),
        now=NOW,
    )
    assert inventory.on_hand(session_factory, principal, clamp.id) == before_stock == 2
    assert len(purchases.list_purchases(session_factory, principal)) == 1
    assert first.source.project_id is not None


def test_read_only_principals_can_preview_but_not_change(
    session_factory: SessionFactory,
    principal: Principal,
    read_only_principal: Principal,
    source: SourceInfo,
    import_dir: Path,
) -> None:
    shown = sync.preview(
        session_factory, read_only_principal, source.id, import_dir=import_dir, now=NOW
    )
    assert shown.plan is not None
    with pytest.raises(ReadOnlyPrincipalError):
        sync.apply(session_factory, read_only_principal, source.id, import_dir=import_dir)
    with pytest.raises(ReadOnlyPrincipalError):
        sync.create_source(
            session_factory,
            read_only_principal,
            sync.SourceCreate(SourceKind.FILE, "heater/project-budget.yaml"),
            import_dir=import_dir,
        )
    with pytest.raises(ReadOnlyPrincipalError):
        sync.detach_source(session_factory, read_only_principal, source.id)


def test_renaming_an_item_keeps_its_catalog_mapping_and_slug(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    renamed = MANIFEST_V1.replace("name: 6 mm fuel line", "name: 6 mm copper fuel line")
    result = sync.apply(
        session_factory,
        principal,
        source.id,
        import_dir=import_dir,
        fetched=fetched(renamed),
        now=NOW,
    )
    assert result.plan is not None
    assert result.plan.creates == ()
    (edit,) = result.plan.updates
    assert [(c.field, c.new) for c in edit.item_changes] == [("name", "6 mm copper fuel line")]
    fuel = catalog.get_item_by_slug(session_factory, principal, "6-mm-fuel-line")
    assert fuel is not None
    assert fuel.name == "6 mm copper fuel line"
    assert catalog.get_item_by_slug(session_factory, principal, "6-mm-copper-fuel-line") is None
    assert len(catalog.list_items(session_factory, principal)) == 2


def test_two_ids_defining_one_new_slug_differently_apply_the_first_and_reject_the_second(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    text = """version: 1
project: {id: diesel-heater, name: Diesel heater install}
items:
  - {id: a, name: Wire, qty: 3, unit: m}
  - {id: b, name: Wire B, qty: 2, unit: ft, catalog: wire}
"""
    result = sync.apply(
        session_factory, principal, source.id, import_dir=import_dir, fetched=fetched(text), now=NOW
    )
    assert result.plan is not None
    assert [c.external_id for c in result.plan.creates] == ["a"]
    assert [r.external_id for r in result.plan.rejects] == ["b"]
    wire = catalog.get_item_by_slug(session_factory, principal, "wire")
    assert wire is not None
    assert wire.unit == "m"
    project_id = result.source.project_id or ""
    assert len(requirements.list_requirements(session_factory, principal, project_id)) == 1


def test_apply_rebuilds_the_plan_under_locks_so_ownership_cannot_change_in_between(
    session_factory: SessionFactory, principal: Principal, source: SourceInfo, import_dir: Path
) -> None:
    sync.apply(session_factory, principal, source.id, import_dir=import_dir, now=NOW)
    fuel = catalog.get_item_by_slug(session_factory, principal, "6-mm-fuel-line")
    assert fuel is not None
    changed = fetched(MANIFEST_V1.replace("unit: m", "unit: ft"))
    shown = sync.preview(
        session_factory, principal, source.id, import_dir=import_dir, fetched=changed, now=NOW
    )
    assert shown.plan is not None
    assert shown.plan.rejects == ()  # at preview time the source still owns the item
    # Stock arrives between preview and apply: the item is now referenced elsewhere.
    inventory.adjust(session_factory, principal, fuel.id, 2, note="found")
    with pytest.raises(sync.PlanChangedError):
        sync.apply(
            session_factory,
            principal,
            source.id,
            import_dir=import_dir,
            fetched=changed,
            expected_content_hash=changed.content_hash,
            expected_plan_digest=shown.plan.digest,
            now=NOW,
        )
    assert catalog.get_item(session_factory, principal, fuel.id).unit == "m"
    # Without a human-approved preview, apply simply applies the plan that is true now.
    result = sync.apply(
        session_factory, principal, source.id, import_dir=import_dir, fetched=changed, now=NOW
    )
    assert result.plan is not None
    assert [r.external_id for r in result.plan.rejects] == ["fuel-line"]
    assert catalog.get_item(session_factory, principal, fuel.id).unit == "m"
