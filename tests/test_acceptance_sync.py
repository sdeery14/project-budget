"""RFC v3 §2 acceptance test 6 and the idempotence test (Milestone 4 gate)."""

from datetime import date, datetime
from pathlib import Path

import pytest

from projectbudget.adapters.sources import FetchedManifest
from projectbudget.domain.catalog import REMOVED_FROM_SOURCE, Intent, RunStatus, SourceKind
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, catalog, inventory, purchases, requirements, sync

NOW = datetime(2026, 11, 8, 9, 0, 0)
AS_OF = date(2026, 11, 1)

MANIFEST = """version: 1
project:
  id: garden-gate
  name: Garden gate
items:
  - id: hinges
    name: Gate hinge set
    qty: 1
    unit: set
    priority: 1
    estimate: { amount: "24.00", currency: USD }
  - id: posts
    name: Fence post
    qty: 4
    estimate: { amount: "18.00", currency: USD }
"""

WITHOUT_POSTS = MANIFEST.split("  - id: posts")[0]


def _fetched(text: str) -> FetchedManifest:
    return FetchedManifest.of(text.encode("utf-8"), revision=None, as_of=AS_OF, description="test")


@pytest.fixture
def source(
    session_factory: SessionFactory, principal: Principal, tmp_path: Path
) -> sync.SourceInfo:
    (tmp_path / "gate.yaml").write_text(MANIFEST, encoding="utf-8")
    return sync.create_source(
        session_factory,
        principal,
        sync.SourceCreate(SourceKind.FILE, "gate.yaml"),
        import_dir=tmp_path,
        now=NOW,
    )


def test_acceptance_6_rerun_changes_nothing_and_a_missing_item_is_archived_with_its_history_intact(
    session_factory: SessionFactory, principal: Principal, source: sync.SourceInfo, tmp_path: Path
) -> None:
    first = sync.apply(session_factory, principal, source.id, import_dir=tmp_path, now=NOW)
    assert first.run.status is RunStatus.APPLIED
    project_id = first.source.project_id or ""
    posts_item = catalog.get_item_by_slug(session_factory, principal, "fence-post")
    assert posts_item is not None
    posts_req = next(
        r
        for r in requirements.list_requirements(session_factory, principal, project_id)
        if r.catalog_item_id == posts_item.id
    )
    # Buy and use two posts: facts that sync must never touch.
    purchase = purchases.mark_purchased(
        session_factory, principal, posts_item.id, 4, Money(1800, "USD"), ordered_on=AS_OF
    )
    inventory.consume(session_factory, principal, posts_req.id, 2)
    backlog_before = backlog.get_backlog(session_factory, principal)

    # Re-running the same manifest changes nothing: a no-op run, identical backlog, no new rows.
    again = sync.apply(session_factory, principal, source.id, import_dir=tmp_path, now=NOW)
    assert again.noop
    assert again.run.status is RunStatus.NOOP
    assert backlog.get_backlog(session_factory, principal) == backlog_before
    assert len(catalog.list_estimates(session_factory, principal, posts_item.id)) == 1
    forced = sync.apply(
        session_factory, principal, source.id, import_dir=tmp_path, force=True, now=NOW
    )
    assert forced.plan is not None
    assert forced.plan.empty
    assert backlog.get_backlog(session_factory, principal) == backlog_before

    # The posts leave the manifest: the requirement is archived, never deleted, and history survives.
    (tmp_path / "gate.yaml").write_text(WITHOUT_POSTS, encoding="utf-8")
    removed = sync.apply(session_factory, principal, source.id, import_dir=tmp_path, now=NOW)
    assert removed.plan is not None
    assert [a.external_id for a in removed.plan.archives] == ["posts"]
    archived = requirements.get_requirement(session_factory, principal, posts_req.id)
    assert (archived.intent, archived.archive_reason) == (Intent.ARCHIVED, REMOVED_FROM_SOURCE)
    assert archived.qty == 4
    assert inventory.consumed(session_factory, principal, posts_req.id) == 2
    assert inventory.on_hand(session_factory, principal, posts_item.id) == 2
    assert (
        purchases.get_purchase(session_factory, principal, purchase.id).lines[0].received_qty == 4
    )
    assert posts_item.id not in {
        row.item.id for row in backlog.get_backlog(session_factory, principal).rows
    }
    hinge_rows = [
        r
        for r in backlog.get_backlog(session_factory, principal).rows
        if r.item.slug == "gate-hinge-set"
    ]
    assert hinge_rows
    assert hinge_rows[0].gross_shortfall == 1


def test_idempotence_applying_the_same_manifest_twice_is_a_fixed_point(
    session_factory: SessionFactory, principal: Principal, source: sync.SourceInfo, tmp_path: Path
) -> None:
    """Apply, then apply again with the hash check bypassed: the second plan is empty and nothing moves."""
    sync.apply(
        session_factory,
        principal,
        source.id,
        import_dir=tmp_path,
        fetched=_fetched(MANIFEST),
        now=NOW,
    )
    snapshot = _snapshot(session_factory, principal)
    second = sync.apply(
        session_factory,
        principal,
        source.id,
        import_dir=tmp_path,
        fetched=_fetched(MANIFEST),
        force=True,
        now=NOW,
    )
    assert second.plan is not None
    assert second.plan.empty
    assert second.plan.counts["unchanged"] == 2
    assert _snapshot(session_factory, principal) == snapshot
    # A new revision date alone does not duplicate estimates either.
    later = FetchedManifest.of(
        MANIFEST.encode("utf-8"), revision=None, as_of=date(2026, 11, 7), description="later"
    )
    third = sync.apply(
        session_factory,
        principal,
        source.id,
        import_dir=tmp_path,
        fetched=later,
        force=True,
        now=NOW,
    )
    assert third.plan is not None
    assert third.plan.empty
    assert _snapshot(session_factory, principal) == snapshot


def _snapshot(session_factory: SessionFactory, principal: Principal) -> tuple[object, ...]:
    items = catalog.list_items(session_factory, principal)
    return (
        tuple((i.slug, i.name, i.kind, i.unit, i.review_state) for i in items),
        tuple(len(catalog.list_estimates(session_factory, principal, i.id)) for i in items),
        backlog.get_backlog(session_factory, principal),
    )
