"""The sync service: sources, preview, apply, no-op, rejects, archive and reappear, ownership, runs."""

import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from projectbudget.adapters.sources import FetchedManifest, SourceFetchError
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
from projectbudget.services import catalog, inventory, projects, purchases, requirements, sync
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
