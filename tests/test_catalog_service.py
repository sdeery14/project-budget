"""Catalog items (slug identity, kind, unit, review state) and price estimates."""

from datetime import date, datetime

import pytest

from projectbudget.domain.catalog import (
    Confidence,
    EstimateOrigin,
    InvalidSlugError,
    InvalidUnitError,
    ItemKind,
    ReviewState,
)
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import catalog
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
