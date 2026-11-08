"""Catalog merge: every reference moves in one transaction and a redirect stays (RFC v3 §4)."""

from datetime import date

import pytest

from projectbudget.domain.catalog import ItemKind, PurchaseStatus
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import backlog, catalog, inventory, projects, purchases, requirements
from projectbudget.services.guards import ReadOnlyPrincipalError


def usd(text: str) -> Money:
    return Money.parse(text, "USD")


@pytest.fixture
def pair(
    session_factory: SessionFactory, principal: Principal
) -> tuple[catalog.CatalogItemInfo, catalog.CatalogItemInfo]:
    """Two spellings of the same bolt, each with requirements, a price, stock and a purchase line."""
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Shed"))
    dupe = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="M8 Bolt", slug="m8-bolt")
    )
    keep = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(name="M8 carriage bolt", slug="m8-carriage-bolt"),
    )
    for item, qty in ((dupe, 10), (keep, 5)):
        requirements.add_requirement(
            session_factory,
            principal,
            requirements.RequirementCreate(project_id=project.id, qty=qty, catalog_item_id=item.id),
        )
    catalog.add_estimate(
        session_factory,
        principal,
        dupe.id,
        catalog.EstimateCreate(price=usd("0.40"), as_of=date(2026, 11, 2)),
    )
    catalog.add_estimate(
        session_factory,
        principal,
        keep.id,
        catalog.EstimateCreate(price=usd("0.45"), as_of=date(2026, 11, 1)),
    )
    inventory.adjust(session_factory, principal, dupe.id, 4, note="old box")
    purchases.record_purchase(
        session_factory,
        principal,
        purchases.PurchaseCreate(
            lines=(purchases.PurchaseLineCreate(dupe.id, 6, usd("0.40")),),
            status=PurchaseStatus.ORDERED,
        ),
    )
    backlog.set_order(session_factory, principal, [dupe.id])
    return dupe, keep


def test_merge_repoints_everything_and_leaves_a_redirect(
    session_factory: SessionFactory,
    principal: Principal,
    pair: tuple[catalog.CatalogItemInfo, catalog.CatalogItemInfo],
) -> None:
    dupe, keep = pair
    before = backlog.get_backlog(session_factory, principal)
    assert {row.item.id for row in before.rows} == {dupe.id, keep.id}

    target = catalog.merge_items(session_factory, principal, dupe.id, keep.id)
    assert target.id == keep.id

    # Requirements, estimates, movements and purchase lines all point at the survivor.
    project_id = requirements.get_requirement(
        session_factory,
        principal,
        backlog.get_backlog(session_factory, principal).rows[0].requirement_ids[0],
    ).project_id
    reqs = requirements.list_requirements(session_factory, principal, project_id)
    assert {r.catalog_item_id for r in reqs} == {keep.id}
    assert sum(r.qty for r in reqs) == 15
    estimates = catalog.list_estimates(session_factory, principal, keep.id)
    assert {str(e.price) for e in estimates} == {"0.40", "0.45"}
    current = catalog.current_estimate(session_factory, principal, keep.id)
    assert current is not None
    assert current.price == usd("0.40")  # the later as_of wins, whichever item it came from
    assert inventory.on_hand(session_factory, principal, keep.id) == 4
    assert inventory.on_hand(session_factory, principal, dupe.id) == 0
    (purchase,) = purchases.list_purchases(session_factory, principal)
    assert purchase.lines[0].catalog_item_id == keep.id

    # One backlog row now: needed 15, on hand 4, shortfall 11, 6 on order; the order slot moved too.
    after = backlog.get_backlog(session_factory, principal)
    (row,) = after.rows
    assert row.item.id == keep.id
    assert (row.needed, row.on_hand, row.gross_shortfall, row.on_order) == (15, 4, 11, 6)
    assert after.order == (keep.id,)

    # The redirect: the old id and slug still resolve, and the old item is read-only.
    merged = catalog.get_item(session_factory, principal, dupe.id)
    assert merged.merged_into_id == keep.id
    assert merged.merged
    assert catalog.get_item_by_slug(session_factory, principal, "m8-bolt") == catalog.get_item(
        session_factory, principal, keep.id
    )
    assert [i.id for i in catalog.list_items(session_factory, principal)] == [keep.id]
    assert len(catalog.list_items(session_factory, principal, include_merged=True)) == 2
    with pytest.raises(catalog.MergedItemError):
        catalog.update_item(
            session_factory, principal, dupe.id, catalog.CatalogItemUpdate(name="x")
        )
    # New references to the old id land on the survivor.
    added = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project_id, qty=1, catalog_item_id=dupe.id),
    )
    assert added.catalog_item_id == keep.id
    typed = requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project_id, qty=1, item_text="M8 Bolt"),
    )
    assert typed.catalog_item_id == keep.id
    priced = catalog.add_estimate(
        session_factory, principal, dupe.id, catalog.EstimateCreate(price=usd("0.50"))
    )
    assert priced.catalog_item_id == keep.id


def test_merge_is_refused_for_mismatched_or_already_merged_items(
    session_factory: SessionFactory, principal: Principal
) -> None:
    metres = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Wire", unit="m")
    )
    feet = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Wire ft", unit="ft")
    )
    tool = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(name="Wire tool", unit="m", kind=ItemKind.REUSABLE),
    )
    with pytest.raises(catalog.MergeError):
        catalog.merge_items(session_factory, principal, metres.id, metres.id)
    with pytest.raises(catalog.MergeError):
        catalog.merge_items(session_factory, principal, metres.id, feet.id)
    with pytest.raises(catalog.MergeError):
        catalog.merge_items(session_factory, principal, metres.id, tool.id)
    with pytest.raises(catalog.CatalogItemNotFoundError):
        catalog.merge_items(session_factory, principal, metres.id, "nope")

    spare = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Wire spare", unit="m")
    )
    catalog.merge_items(session_factory, principal, spare.id, metres.id)
    with pytest.raises(catalog.MergedItemError):
        catalog.merge_items(session_factory, principal, spare.id, feet.id)
    with pytest.raises(catalog.MergedItemError):
        catalog.merge_items(session_factory, principal, feet.id, spare.id)


def test_merge_is_all_or_nothing(
    session_factory: SessionFactory,
    principal: Principal,
    pair: tuple[catalog.CatalogItemInfo, catalog.CatalogItemInfo],
) -> None:
    """A refused merge changes nothing: the first item still owns all its references."""
    dupe, keep = pair
    catalog.update_item(
        session_factory, principal, keep.id, catalog.CatalogItemUpdate(kind=ItemKind.REUSABLE)
    )
    with pytest.raises(catalog.MergeError):
        catalog.merge_items(session_factory, principal, dupe.id, keep.id)
    assert inventory.on_hand(session_factory, principal, dupe.id) == 4
    assert not catalog.get_item(session_factory, principal, dupe.id).merged
    assert {row.item.id for row in backlog.get_backlog(session_factory, principal).rows} == {
        dupe.id,
        keep.id,
    }


def test_read_only_principals_cannot_merge(
    session_factory: SessionFactory,
    principal: Principal,
    read_only_principal: Principal,
    pair: tuple[catalog.CatalogItemInfo, catalog.CatalogItemInfo],
) -> None:
    dupe, keep = pair
    with pytest.raises(ReadOnlyPrincipalError):
        catalog.merge_items(session_factory, read_only_principal, dupe.id, keep.id)
