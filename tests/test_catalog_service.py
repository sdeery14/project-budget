"""Catalog items (slug identity, kind, unit, review state) and price estimates."""

from datetime import date, datetime

import pytest

from projectbudget.domain.catalog import (
    Confidence,
    EstimateOrigin,
    InvalidSlugError,
    InvalidUnitError,
    ItemKind,
    PurchaseStatus,
    ReviewState,
)
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import catalog, inventory, projects, purchases, requirements
from projectbudget.services.clock import today
from projectbudget.services.guards import ReadOnlyPrincipalError

NOW = datetime(2026, 11, 8, 9, 0, 0)
LATER = datetime(2026, 11, 8, 10, 0, 0)


def test_create_item_derives_the_slug_and_defaults(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name=" M5 x 20  Bolts "), now=NOW
    )
    assert item.name == "M5 x 20 Bolts"
    assert item.slug == "m5-x-20-bolts"
    assert item.kind is ItemKind.CONSUMABLE
    assert item.unit == "each"
    assert item.review_state is ReviewState.CONFIRMED
    assert item.created_at == item.updated_at == NOW
    assert catalog.get_item(session_factory, principal, item.id) == item
    assert catalog.get_item_by_slug(session_factory, principal, "m5-x-20-bolts") == item
    assert catalog.get_item_by_slug(session_factory, principal, "nothing") is None


def test_explicit_slug_is_validated_and_unique(
    session_factory: SessionFactory, principal: Principal
) -> None:
    clamp = catalog.create_item(
        session_factory,
        principal,
        catalog.CatalogItemCreate(
            name="Bar clamp 600 mm", slug="clamp-600", kind=ItemKind.REUSABLE
        ),
    )
    assert clamp.slug == "clamp-600"
    with pytest.raises(catalog.SlugTakenError):
        catalog.create_item(
            session_factory, principal, catalog.CatalogItemCreate(name="Other", slug="clamp-600")
        )
    with pytest.raises(catalog.SlugTakenError):
        catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp 600"))
    with pytest.raises(InvalidSlugError):
        catalog.create_item(
            session_factory, principal, catalog.CatalogItemCreate(name="X", slug="Not A Slug")
        )
    assert [i.slug for i in catalog.list_items(session_factory, principal)] == ["clamp-600"]


def test_update_changes_everything_but_the_slug(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Clamp"), now=NOW
    )
    updated = catalog.update_item(
        session_factory,
        principal,
        item.id,
        catalog.CatalogItemUpdate(
            name="Bar clamp",
            kind=ItemKind.REUSABLE,
            unit=" Pair ",
            review_state=ReviewState.UNREVIEWED,
        ),
        now=LATER,
    )
    assert updated.slug == "clamp"
    assert updated.name == "Bar clamp"
    assert updated.kind is ItemKind.REUSABLE
    assert updated.unit == "pair"
    assert updated.review_state is ReviewState.UNREVIEWED
    assert updated.updated_at == LATER
    with pytest.raises(InvalidUnitError):
        catalog.update_item(
            session_factory, principal, item.id, catalog.CatalogItemUpdate(unit="two words")
        )
    with pytest.raises(catalog.CatalogItemNotFoundError):
        catalog.update_item(session_factory, principal, "nope", catalog.CatalogItemUpdate(name="x"))


def test_latest_as_of_wins_and_ties_go_to_the_newest_record(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp"))
    old = catalog.add_estimate(
        session_factory,
        principal,
        item.id,
        catalog.EstimateCreate(price=Money(1000, "USD"), as_of=date(2026, 1, 1)),
        now=NOW,
    )
    newest_date = catalog.add_estimate(
        session_factory,
        principal,
        item.id,
        catalog.EstimateCreate(
            price=Money(1200, "USD"),
            as_of=date(2026, 6, 1),
            confidence=Confidence.HIGH,
            origin=EstimateOrigin.PURCHASE,
        ),
        now=NOW,
    )
    same_date_recorded_later = catalog.add_estimate(
        session_factory,
        principal,
        item.id,
        catalog.EstimateCreate(price=Money(1150, "USD"), as_of=date(2026, 6, 1)),
        now=LATER,
    )
    current = catalog.current_estimate(session_factory, principal, item.id)
    assert current == same_date_recorded_later
    assert current.price == Money(1150, "USD")
    listed = catalog.list_estimates(session_factory, principal, item.id)
    assert listed == [same_date_recorded_later, newest_date, old]
    assert newest_date.confidence is Confidence.HIGH
    assert newest_date.origin is EstimateOrigin.PURCHASE
    assert old.confidence is Confidence.MEDIUM
    assert old.origin is EstimateOrigin.MANUAL


def test_estimate_defaults_as_of_to_today_and_rejects_negatives(
    session_factory: SessionFactory, principal: Principal
) -> None:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp"))
    saved = catalog.add_estimate(
        session_factory, principal, item.id, catalog.EstimateCreate(price=Money.zero("USD"))
    )
    assert saved.as_of == today()
    with pytest.raises(catalog.NegativeEstimateError):
        catalog.add_estimate(
            session_factory, principal, item.id, catalog.EstimateCreate(price=Money(-1, "USD"))
        )
    with pytest.raises(catalog.CatalogItemNotFoundError):
        catalog.add_estimate(
            session_factory, principal, "nope", catalog.EstimateCreate(price=Money(1, "USD"))
        )
    with pytest.raises(catalog.CatalogItemNotFoundError):
        catalog.current_estimate(session_factory, principal, "nope")


def test_read_only_principals_cannot_write(
    session_factory: SessionFactory, principal: Principal, read_only_principal: Principal
) -> None:
    item = catalog.create_item(session_factory, principal, catalog.CatalogItemCreate(name="Clamp"))
    with pytest.raises(ReadOnlyPrincipalError):
        catalog.create_item(
            session_factory, read_only_principal, catalog.CatalogItemCreate(name="Saw")
        )
    with pytest.raises(ReadOnlyPrincipalError):
        catalog.update_item(
            session_factory, read_only_principal, item.id, catalog.CatalogItemUpdate(name="x")
        )
    with pytest.raises(ReadOnlyPrincipalError):
        catalog.add_estimate(
            session_factory,
            read_only_principal,
            item.id,
            catalog.EstimateCreate(price=Money(1, "USD")),
        )
    assert catalog.list_items(session_factory, read_only_principal) == [item]
    assert catalog.current_estimate(session_factory, read_only_principal, item.id) is None


def test_unit_cannot_change_while_requirements_or_estimates_use_it(
    session_factory: SessionFactory, principal: Principal
) -> None:
    """10 metres at 1.00 a metre must never silently become 10 feet at 1.00 a foot."""
    wire = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Wire", unit="m")
    )
    project = projects.create_project(session_factory, principal, projects.ProjectCreate("Shed"))
    requirements.add_requirement(
        session_factory,
        principal,
        requirements.RequirementCreate(project_id=project.id, qty=10, catalog_item_id=wire.id),
    )
    with pytest.raises(catalog.UnitInUseError):
        catalog.update_item(
            session_factory, principal, wire.id, catalog.CatalogItemUpdate(unit="ft")
        )
    # The same unit spelled differently is not a change; other fields still update.
    renamed = catalog.update_item(
        session_factory,
        principal,
        wire.id,
        catalog.CatalogItemUpdate(name="Copper wire", unit=" M "),
    )
    assert (renamed.name, renamed.unit) == ("Copper wire", "m")

    priced = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Rope", unit="m")
    )
    catalog.add_estimate(
        session_factory, principal, priced.id, catalog.EstimateCreate(price=Money(100, "USD"))
    )
    with pytest.raises(catalog.UnitInUseError):
        catalog.update_item(
            session_factory, principal, priced.id, catalog.CatalogItemUpdate(unit="ft")
        )
    assert catalog.get_item(session_factory, principal, priced.id).unit == "m"

    unused = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Tape", unit="m")
    )
    assert (
        catalog.update_item(
            session_factory, principal, unused.id, catalog.CatalogItemUpdate(unit="roll")
        ).unit
        == "roll"
    )


def test_unit_cannot_change_once_stock_movements_exist_even_at_zero_stock(
    session_factory: SessionFactory, principal: Principal
) -> None:
    """The history "received 10 m, lost 10 m" is in metres and must stay in metres."""
    rope = catalog.create_item(
        session_factory, principal, catalog.CatalogItemCreate(name="Rope", unit="m")
    )
    inventory.adjust(session_factory, principal, rope.id, 10, note="stock take")
    with pytest.raises(catalog.UnitInUseError):
        catalog.update_item(
            session_factory, principal, rope.id, catalog.CatalogItemUpdate(unit="ft")
        )
    inventory.adjust(session_factory, principal, rope.id, -10, note="used up")
    assert inventory.on_hand(session_factory, principal, rope.id) == 0
    with pytest.raises(catalog.UnitInUseError):
        catalog.update_item(
            session_factory, principal, rope.id, catalog.CatalogItemUpdate(unit="ft")
        )
    assert catalog.get_item(session_factory, principal, rope.id).unit == "m"


def test_unit_cannot_change_once_purchase_lines_reference_the_item_in_any_status(
    session_factory: SessionFactory, principal: Principal
) -> None:
    """10 metres at 1.00 a metre on order must not become 10 feet at 1.00 a foot."""
    for status in (PurchaseStatus.ORDERED, PurchaseStatus.PLANNED, PurchaseStatus.CANCELLED):
        rope = catalog.create_item(
            session_factory,
            principal,
            catalog.CatalogItemCreate(name=f"Rope {status.value}", unit="m"),
        )
        purchases.record_purchase(
            session_factory,
            principal,
            purchases.PurchaseCreate(
                lines=(purchases.PurchaseLineCreate(rope.id, 10, Money(100, "USD")),),
                status=status,
            ),
        )
        assert inventory.on_hand(session_factory, principal, rope.id) == 0
        with pytest.raises(catalog.UnitInUseError):
            catalog.update_item(
                session_factory, principal, rope.id, catalog.CatalogItemUpdate(unit="ft")
            )
        assert catalog.get_item(session_factory, principal, rope.id).unit == "m"
