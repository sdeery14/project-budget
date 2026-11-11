"""The pure sync engine: every row of the reconciliation table in RFC v3 §6 (ADR-007)."""

from datetime import date

from projectbudget.domain.catalog import Intent, ItemKind, ReviewState, Sharing
from projectbudget.domain.manifest import Manifest, ManifestEstimate, ManifestItem
from projectbudget.domain.money import Money
from projectbudget.domain.sync import (
    CatalogState,
    ExistingRequirement,
    ManualRequirement,
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
    sort_order: int = 0,
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
        sort_order=sort_order,
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
    manual: tuple[ManualRequirement, ...] = (),
    attached: bool = False,
    same_name: tuple[str, ...] = (),
) -> SyncContext:
    manifest = Manifest(version=1, project_id="shed", project_name="Shed", items=tuple(items))
    return SyncContext(
        currency="USD",
        manifest=manifest,
        existing=existing_reqs,
        catalog=catalog or {},
        as_of=AS_OF,
        manual=manual,
        attached=attached,
        same_name_projects=same_name,
    )


def manual(
    id_: str, item_id: str, name: str, qty: int = 1, *, priority: int = 2, sort_order: int = 0
) -> ManualRequirement:
    return ManualRequirement(
        id=id_,
        catalog_item_id=item_id,
        qty=qty,
        priority=priority,
        sort_order=sort_order,
        item_name=name,
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


def test_the_file_order_is_the_requirement_order() -> None:
    """ADR-012 (#34): creates carry their position; a moved existing item gets a sort_order change."""
    plan = plan_sync(
        ctx(
            item("b", "Bee"),
            item("a", "Ay"),
            item("c", "Sea"),
            existing_reqs=(
                existing("a", "Ay", sort_order=0, item_id="item-ay"),
                existing("b", "Bee", sort_order=1, item_id="item-bee"),
            ),
            catalog={"ay": state("ay", "Ay"), "bee": state("bee", "Bee")},
        )
    )
    assert [
        (u.external_id, [(c.field, c.old, c.new) for c in u.changes]) for u in plan.updates
    ] == [
        ("b", [("sort_order", 1, 0)]),
        ("a", [("sort_order", 0, 1)]),
    ]
    (create,) = plan.creates
    assert (create.external_id, create.sort_order) == ("c", 2)
    assert plan.unchanged == ()


def test_a_manual_requirement_for_the_same_item_is_adopted_not_duplicated() -> None:
    """ADR-012 (#26): attached project; the manifest takes qty, priority and order, keeps the rest."""
    plan = plan_sync(
        ctx(
            item("saw", "Hand saw", 1),
            item("clamps", "Hose clamp", 6, priority=1, amount="2.00"),
            item("glue", "Wood glue", 1),
            catalog={
                "hose-clamp": state(
                    "hose-clamp", "Hose clamp", mine=False, review_state=ReviewState.CONFIRMED
                ),
                "hand-saw": state("hand-saw", "Hand saw", mine=False, elsewhere=True),
            },
            manual=(
                manual("req-saw", "item-hand-saw", "Hand saw", 1, sort_order=0),
                manual("req-clamp", "item-hose-clamp", "Hose clamp", 2, priority=3, sort_order=1),
                manual("req-clamp-2", "item-hose-clamp", "Hose clamp", 9, sort_order=2),
                manual("req-tape", "item-tape", "Tape", 1, sort_order=3),
            ),
            attached=True,
        )
    )
    assert [(a.requirement_id, a.external_id) for a in plan.adoptions] == [
        ("req-saw", "saw"),
        ("req-clamp", "clamps"),
    ]
    saw, clamp = plan.adoptions
    assert saw.changes == ()  # same qty, priority and position already
    assert [(c.field, c.old, c.new) for c in clamp.changes] == [
        ("qty", 2, 6),
        ("priority", 3, 1),
    ]
    assert clamp.estimate == Money(200, "USD")
    # The second manual clamp requirement and the tape stay manual; glue is new.
    assert [c.external_id for c in plan.creates] == ["glue"]
    assert plan.counts["adoptions"] == 2
    assert plan.warnings == ()
    assert not plan.empty


def test_repeated_catalog_items_adopt_the_manual_rows_in_project_order() -> None:
    """Two manifest items for one catalog item take two manual rows; nothing is created twice."""
    plan = plan_sync(
        ctx(
            item("front", "Hose clamp", 3, catalog="hose-clamp"),
            item("rear", "Hose clamp", 4, catalog="hose-clamp"),
            catalog={"hose-clamp": state("hose-clamp", "Hose clamp", mine=False)},
            manual=(
                manual("req-1", "item-hose-clamp", "Hose clamp", 2, sort_order=0),
                manual("req-2", "item-hose-clamp", "Hose clamp", 7, sort_order=1),
            ),
            attached=True,
        )
    )
    assert [(a.requirement_id, a.external_id) for a in plan.adoptions] == [
        ("req-1", "front"),
        ("req-2", "rear"),
    ]
    assert [[(c.field, c.old, c.new) for c in a.changes] for a in plan.adoptions] == [
        [("qty", 2, 3)],
        [("qty", 7, 4)],
    ]
    assert plan.creates == ()

    # One manual row, two manifest items: the second is a create, the first an adoption.
    one = plan_sync(
        ctx(
            item("front", "Hose clamp", 3, catalog="hose-clamp"),
            item("rear", "Hose clamp", 4, catalog="hose-clamp"),
            catalog={"hose-clamp": state("hose-clamp", "Hose clamp", mine=False)},
            manual=(manual("req-1", "item-hose-clamp", "Hose clamp", 2),),
            attached=True,
        )
    )
    assert [a.external_id for a in one.adoptions] == ["front"]
    assert [c.external_id for c in one.creates] == ["rear"]


def test_adoption_still_rejects_a_kind_or_unit_the_source_does_not_own() -> None:
    plan = plan_sync(
        ctx(
            item("clamps", "Hose clamp", 6, unit="box"),
            catalog={"hose-clamp": state("hose-clamp", "Hose clamp", mine=False)},
            manual=(manual("req-clamp", "item-hose-clamp", "Hose clamp", 2),),
            attached=True,
        )
    )
    assert plan.adoptions == ()
    assert [r.external_id for r in plan.rejects] == ["clamps"]


def test_an_unattached_source_warns_when_a_project_with_the_manifest_name_exists() -> None:
    plan = plan_sync(ctx(item("a", "A"), same_name=("Shed",)))
    (warning,) = plan.warnings
    assert "'Shed' already exists" in warning
    assert "detach this source and add it again" in warning
    assert plan_sync(ctx(item("a", "A"), same_name=("Shed",), attached=True)).warnings == ()
    assert plan_sync(ctx(item("a", "A"))).warnings == ()


def test_counts_summarise_the_plan() -> None:
    plan = plan_sync(
        ctx(
            item("new", "New"),
            item("same", "Same"),
            item("back", "Back"),
            existing_reqs=(
                existing("same", "Same", sort_order=1),
                existing("gone", "Gone"),
                existing(
                    "back",
                    "Back",
                    intent=Intent.ARCHIVED,
                    archive_reason="removed_from_source",
                    sort_order=2,
                ),
            ),
            catalog={"same": state("same", "Same"), "back": state("back", "Back")},
        )
    )
    assert plan.counts == {
        "creates": 1,
        "adoptions": 0,
        "updates": 0,
        "reactivations": 1,
        "archives": 1,
        "rejects": 0,
        "estimate_rejects": 0,
        "unchanged": 1,
    }


def test_conflicting_definitions_of_one_new_slug_inside_a_manifest_reject_the_later_item() -> None:
    plan = plan_sync(
        ctx(
            item("a", "Wire", 3, unit="m"),
            item("b", "Copper", 2, unit="ft", catalog="wire"),
            item("c", "Wire again", 1, unit="m", catalog="wire"),
        )
    )
    assert [c.external_id for c in plan.creates] == [
        "a",
        "c",
    ]  # same definition: both share the item
    (reject,) = plan.rejects
    assert reject.external_id == "b"
    assert "conflicts with item 'a' in this manifest" in reject.reason
    # The conflict is also caught against an item the source owns and is about to change.
    owned = plan_sync(
        ctx(
            item("a", "Wire", 3, unit="ft"),
            item("b", "Wire two", 1, unit="m", catalog="wire"),
            existing_reqs=(existing("a", "Wire"),),
            catalog={"wire": state("wire", "Wire", unit="m")},
        )
    )
    assert [u.external_id for u in owned.updates] == ["a"]
    assert [r.external_id for r in owned.rejects] == ["b"]


def test_existing_requirements_keep_their_catalog_mapping_when_the_name_changes() -> None:
    manifest = Manifest(
        version=1,
        project_id="shed",
        project_name="Shed",
        items=(item("w", "Copper Wire", 2, unit="m"),),
    )
    plan = plan_sync(
        SyncContext(
            currency="USD",
            manifest=manifest,
            existing=(existing("w", "Wire", 2, item_id="item-wire"),),
            catalog={},  # "copper-wire" does not exist; the id's mapping decides, not the name
            as_of=AS_OF,
            items_by_id={"item-wire": state("wire", "Wire", unit="m")},
        )
    )
    assert plan.creates == ()
    (update,) = plan.updates
    assert [(c.field, c.old, c.new) for c in update.item_changes] == [
        ("name", "Wire", "Copper Wire")
    ]
    assert update.changes == ()
    # A confirmed item keeps the owner's name and the rename is simply nothing to do.
    confirmed = plan_sync(
        SyncContext(
            currency="USD",
            manifest=manifest,
            existing=(existing("w", "Wire", 2, item_id="item-wire"),),
            catalog={},
            as_of=AS_OF,
            items_by_id={
                "item-wire": state("wire", "Wire", unit="m", review_state=ReviewState.CONFIRMED)
            },
        )
    )
    assert confirmed.updates == ()
    assert confirmed.unchanged == ("w",)
