"""The pure sync engine: every row of the reconciliation table in RFC v3 §6 (ADR-007)."""

from datetime import date

from projectbudget.domain.catalog import Intent, ItemKind, ReviewState, Sharing
from projectbudget.domain.manifest import Manifest, ManifestEstimate, ManifestItem
from projectbudget.domain.money import Money
from projectbudget.domain.sync import (
    CatalogState,
    ExistingRequirement,
    SyncContext,
    plan_sync,
    slug_for,
)

AS_OF = date(2026, 11, 1)


def item(
    id_: str,
    name: str,
    qty: int = 1,
    *,
    unit: str = "each",
    kind: ItemKind = ItemKind.CONSUMABLE,
    priority: int = 2,
    sharing: Sharing = Sharing.SHARED,
    amount: str | None = None,
    currency: str | None = None,
    catalog: str | None = None,
) -> ManifestItem:
    return ManifestItem(
        id=id_,
        name=name,
        qty=qty,
        unit=unit,
        kind=kind,
        priority=priority,
        sharing=sharing,
        estimate=ManifestEstimate(amount=amount, currency=currency) if amount else None,
        catalog=catalog,
        notes="",
    )


def existing(
    external_id: str,
    name: str,
    qty: int = 1,
    *,
    priority: int = 2,
    intent: Intent = Intent.ACTIVE,
    archive_reason: str | None = None,
    item_id: str = "item",
) -> ExistingRequirement:
    return ExistingRequirement(
        id=f"req-{external_id}",
        external_id=external_id,
        catalog_item_id=item_id,
        qty=qty,
        priority=priority,
        sharing=Sharing.SHARED,
        intent=intent,
        archive_reason=archive_reason,
        item_name=name,
    )


def state(
    slug: str,
    name: str,
    *,
    kind: ItemKind = ItemKind.CONSUMABLE,
    unit: str = "each",
    review_state: ReviewState = ReviewState.UNREVIEWED,
    merged: bool = False,
    mine: bool = True,
    elsewhere: bool = False,
    manifest_estimate: Money | None = None,
) -> CatalogState:
    return CatalogState(
        id=f"item-{slug}",
        slug=slug,
        name=name,
        kind=kind,
        unit=unit,
        review_state=review_state,
        merged=merged,
        created_by_this_source=mine,
        referenced_elsewhere=elsewhere,
        manifest_estimate=manifest_estimate,
    )


def ctx(
    *items: ManifestItem,
    existing_reqs: tuple[ExistingRequirement, ...] = (),
    catalog: dict[str, CatalogState] | None = None,
) -> SyncContext:
    manifest = Manifest(version=1, project_id="shed", project_name="Shed", items=tuple(items))
    return SyncContext(
        currency="USD",
        manifest=manifest,
        existing=existing_reqs,
        catalog=catalog or {},
        as_of=AS_OF,
    )


def test_new_item_creates_a_requirement_and_an_unreviewed_catalog_item_unless_the_slug_exists() -> (
    None
):
    plan = plan_sync(
        ctx(item("a", "Fuel line", 3, amount="12.50"), item("b", "Clamp", catalog="hose-clamp"))
    )
    assert [c.external_id for c in plan.creates] == ["a", "b"]
    fuel, clamp = plan.creates
    assert (fuel.slug, fuel.catalog_exists, fuel.estimate) == (
        "fuel-line",
        False,
        Money(1250, "USD"),
    )
    assert (clamp.slug, clamp.catalog_exists) == ("hose-clamp", False)
    matched = plan_sync(
        ctx(
            item("b", "Clamp", catalog="hose-clamp"),
            catalog={
                "hose-clamp": state(
                    "hose-clamp", "Hose clamp", mine=False, review_state=ReviewState.CONFIRMED
                )
            },
        )
    )
    assert matched.creates[0].catalog_exists is True
    assert matched.rejects == ()
    assert slug_for(item("x", "6 mm Fuel Line")) == "6-mm-fuel-line"


def test_existing_id_with_changed_fields_updates_source_owned_fields_only() -> None:
    plan = plan_sync(
        ctx(
            item("a", "Fuel line", 5, priority=1, sharing=Sharing.EXCLUSIVE),
            existing_reqs=(existing("a", "Fuel line", 3, priority=2),),
            catalog={"fuel-line": state("fuel-line", "Fuel line")},
        )
    )
    (update,) = plan.updates
    assert update.requirement_id == "req-a"
    assert [(c.field, c.old, c.new) for c in update.changes] == [("qty", 3, 5), ("priority", 2, 1)]
    assert update.item_changes == ()  # sharing is an app-owned override after creation
    assert not update.reactivate
    assert plan.empty is False


def test_unchanged_item_is_reported_as_unchanged_and_the_plan_can_be_empty() -> None:
    plan = plan_sync(
        ctx(
            item("a", "Fuel line", 3, amount="12.50"),
            existing_reqs=(existing("a", "Fuel line", 3),),
            catalog={
                "fuel-line": state("fuel-line", "Fuel line", manifest_estimate=Money(1250, "USD"))
            },
        )
    )
    assert plan.unchanged == ("a",)
    assert plan.empty
    assert plan.counts["unchanged"] == 1


def test_id_missing_from_the_manifest_archives_and_a_reappearing_id_unarchives() -> None:
    gone = plan_sync(ctx(existing_reqs=(existing("a", "Fuel line"),)))
    assert [(a.requirement_id, a.external_id, a.item_name) for a in gone.archives] == [
        ("req-a", "a", "Fuel line")
    ]
    already = plan_sync(
        ctx(
            existing_reqs=(
                existing(
                    "a", "Fuel line", intent=Intent.ARCHIVED, archive_reason="removed_from_source"
                ),
            )
        )
    )
    assert already.archives == ()
    back = plan_sync(
        ctx(
            item("a", "Fuel line"),
            existing_reqs=(
                existing(
                    "a", "Fuel line", intent=Intent.ARCHIVED, archive_reason="removed_from_source"
                ),
            ),
            catalog={"fuel-line": state("fuel-line", "Fuel line")},
        )
    )
    (update,) = back.updates
    assert update.reactivate is True
    assert back.reactivations == (update,)
    # Archived by the owner for another reason: intent is app-owned, the manifest does not reopen it.
    owner_archived = plan_sync(
        ctx(
            item("a", "Fuel line"),
            existing_reqs=(
                existing("a", "Fuel line", intent=Intent.ARCHIVED, archive_reason="done"),
            ),
            catalog={"fuel-line": state("fuel-line", "Fuel line")},
        )
    )
    assert owner_archived.updates == ()
    assert owner_archived.unchanged == ("a",)


def test_renamed_item_with_the_same_id_updates_the_name_when_the_source_owns_the_item() -> None:
    owned = plan_sync(
        ctx(
            item("a", "Fuel hose"),
            existing_reqs=(existing("a", "Fuel line"),),
            catalog={"fuel-hose": state("fuel-hose", "Fuel line")},
        )
    )
    (update,) = owned.updates
    assert [(c.field, c.old, c.new) for c in update.item_changes] == [
        ("name", "Fuel line", "Fuel hose")
    ]
    confirmed = plan_sync(
        ctx(
            item("a", "Fuel hose"),
            existing_reqs=(existing("a", "Fuel line"),),
            catalog={
                "fuel-hose": state("fuel-hose", "Fuel line", review_state=ReviewState.CONFIRMED)
            },
        )
    )
    assert confirmed.updates == ()  # a confirmed item keeps the owner's name; nothing else changed


def test_same_name_under_a_new_id_creates_and_archives_and_flags_the_likely_rename() -> None:
    plan = plan_sync(
        ctx(item("a2", "Fuel line", 3), existing_reqs=(existing("a1", "Fuel line", 3),))
    )
    assert [c.external_id for c in plan.creates] == ["a2"]
    assert plan.creates[0].likely_rename_of == "a1"
    assert [a.external_id for a in plan.archives] == ["a1"]


def test_kind_or_unit_differing_on_an_item_the_source_does_not_own_rejects_that_item_only() -> None:
    confirmed = state("wire", "Wire", unit="m", review_state=ReviewState.CONFIRMED)
    merged = state("tape", "Tape", kind=ItemKind.CONSUMABLE, merged=True)
    shared = state("glue", "Glue", unit="tube", elsewhere=True)
    foreign = state("saw", "Saw", kind=ItemKind.REUSABLE, mine=False)
    plan = plan_sync(
        ctx(
            item("w", "Wire", unit="ft"),
            item("t", "Tape", kind=ItemKind.REUSABLE),
            item("g", "Glue", unit="each"),
            item("s", "Saw", kind=ItemKind.CONSUMABLE),
            item("ok", "Bolt"),
            catalog={"wire": confirmed, "tape": merged, "glue": shared, "saw": foreign},
        )
    )
    assert sorted(r.external_id for r in plan.rejects) == ["g", "s", "t", "w"]
    assert "unit 'm' vs 'ft'" in next(r.reason for r in plan.rejects if r.external_id == "w")
    assert [c.external_id for c in plan.creates] == ["ok"]


def test_kind_and_unit_follow_the_manifest_while_the_source_owns_the_item() -> None:
    plan = plan_sync(
        ctx(
            item("w", "Wire", unit="ft", kind=ItemKind.REUSABLE),
            existing_reqs=(existing("w", "Wire"),),
            catalog={"wire": state("wire", "Wire", unit="m")},
        )
    )
    (update,) = plan.updates
    assert [(c.field, c.new) for c in update.item_changes] == [("kind", "reusable"), ("unit", "ft")]


def test_estimate_currency_mismatch_rejects_only_the_estimate() -> None:
    plan = plan_sync(ctx(item("a", "Fuel line", 3, amount="12.50", currency="EUR")))
    assert [c.external_id for c in plan.creates] == ["a"]
    assert plan.creates[0].estimate is None
    assert plan.estimate_rejects[0].external_id == "a"
    assert "EUR" in plan.estimate_rejects[0].reason
    assert plan.rejects == ()


def test_estimate_is_recorded_only_when_it_differs_from_the_last_manifest_estimate() -> None:
    same = plan_sync(
        ctx(
            item("a", "Fuel line", 3, amount="12.50"),
            existing_reqs=(existing("a", "Fuel line", 3),),
            catalog={
                "fuel-line": state("fuel-line", "Fuel line", manifest_estimate=Money(1250, "USD"))
            },
        )
    )
    assert same.updates == ()
    changed = plan_sync(
        ctx(
            item("a", "Fuel line", 3, amount="13.00"),
            existing_reqs=(existing("a", "Fuel line", 3),),
            catalog={
                "fuel-line": state("fuel-line", "Fuel line", manifest_estimate=Money(1250, "USD"))
            },
        )
    )
    assert changed.updates[0].estimate == Money(1300, "USD")
    assert changed.updates[0].changes == ()


def test_a_name_that_yields_no_slug_is_rejected() -> None:
    plan = plan_sync(ctx(item("x", "日本語")))
    assert plan.rejects[0].external_id == "x"
    assert "slug" in plan.rejects[0].reason


def test_counts_summarise_the_plan() -> None:
    plan = plan_sync(
        ctx(
            item("new", "New"),
            item("same", "Same"),
            item("back", "Back"),
            existing_reqs=(
                existing("same", "Same"),
                existing("gone", "Gone"),
                existing(
                    "back", "Back", intent=Intent.ARCHIVED, archive_reason="removed_from_source"
                ),
            ),
            catalog={"same": state("same", "Same"), "back": state("back", "Back")},
        )
    )
    assert plan.counts == {
        "creates": 1,
        "updates": 0,
        "reactivations": 1,
        "archives": 1,
        "rejects": 0,
        "estimate_rejects": 0,
        "unchanged": 1,
    }
