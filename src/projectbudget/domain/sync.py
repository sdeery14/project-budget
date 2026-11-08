"""The sync plan: what applying a manifest would change (RFC v3 §6, ADR-007). Pure.

The service gathers the current state (synced requirements of this source, the
catalog items the manifest names, the workspace currency, the revision date)
and this module produces the diff the UI shows before apply. Every row of the
reconciliation table in §6 is a named test in ``tests/test_sync_plan.py``.

Ownership (ADR-007): the source owns name, qty, priority, the sharing default
and the estimate; it owns kind and unit only for an item it created that is
still unreviewed and referenced by nothing else. The app owns intent, the
catalog mapping, the sharing override, notes and sort order.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from projectbudget.domain.catalog import (
    REMOVED_FROM_SOURCE,
    Intent,
    ItemKind,
    ReviewState,
    Sharing,
    slugify,
)
from projectbudget.domain.manifest import Manifest, ManifestItem
from projectbudget.domain.money import CurrencyMismatchError, Money, MoneyParseError


@dataclass(frozen=True, slots=True)
class ExistingRequirement:
    """A requirement this source created earlier, keyed by its external id."""

    id: str
    external_id: str
    catalog_item_id: str
    qty: int
    priority: int
    sharing: Sharing
    intent: Intent
    archive_reason: str | None
    item_name: str


@dataclass(frozen=True, slots=True)
class CatalogState:
    """What the engine needs to know about a catalog item the manifest names."""

    id: str
    slug: str
    name: str
    kind: ItemKind
    unit: str
    review_state: ReviewState
    merged: bool
    created_by_this_source: bool
    referenced_elsewhere: bool
    """Requirements of other sources or manual ones, purchase lines, movements, manual estimates."""
    manifest_estimate: Money | None = None
    """The latest estimate this source already recorded for the item."""

    @property
    def source_owned(self) -> bool:
        """Kind, unit and name follow the manifest only while all four conditions hold."""
        return (
            self.created_by_this_source
            and self.review_state is ReviewState.UNREVIEWED
            and not self.merged
            and not self.referenced_elsewhere
        )


@dataclass(frozen=True, slots=True)
class SyncContext:
    currency: str
    manifest: Manifest
    existing: tuple[ExistingRequirement, ...]
    catalog: Mapping[str, CatalogState]
    """Live catalog items by slug (merge redirects already followed), for items that are new here."""
    as_of: date
    items_by_id: Mapping[str, CatalogState] = field(default_factory=dict)
    """Live catalog items by id, for the requirements this source already has. Identity, not names."""


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    old: object
    new: object


@dataclass(frozen=True, slots=True)
class Create:
    external_id: str
    item: ManifestItem
    slug: str
    catalog_exists: bool
    estimate: Money | None
    likely_rename_of: str | None = None
    """An existing requirement with the same item name that is leaving the manifest."""


@dataclass(frozen=True, slots=True)
class Update:
    requirement_id: str
    external_id: str
    changes: tuple[FieldChange, ...]
    """Requirement fields: qty, priority."""
    item_changes: tuple[FieldChange, ...]
    """Catalog item fields the source owns here: name, kind, unit."""
    estimate: Money | None
    """A new estimate to record, or None when nothing changed."""
    reactivate: bool = False
    """The requirement was archived as removed_from_source and comes back."""


@dataclass(frozen=True, slots=True)
class Archive:
    requirement_id: str
    external_id: str
    item_name: str


@dataclass(frozen=True, slots=True)
class Reject:
    external_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class SyncPlan:
    project_id: str
    project_name: str
    creates: tuple[Create, ...] = ()
    updates: tuple[Update, ...] = ()
    archives: tuple[Archive, ...] = ()
    rejects: tuple[Reject, ...] = ()
    """Whole items refused: nothing about them is applied."""
    estimate_rejects: tuple[Reject, ...] = ()
    """Items whose estimate was refused while their other fields apply."""
    unchanged: tuple[str, ...] = ()

    @property
    def reactivations(self) -> tuple[Update, ...]:
        return tuple(u for u in self.updates if u.reactivate)

    @property
    def digest(self) -> str:
        """A fingerprint of exactly what this plan would do; apply checks it against the preview."""
        return hashlib.sha256(repr(self).encode("utf-8")).hexdigest()

    @property
    def empty(self) -> bool:
        """Nothing to write: applying would change no row."""
        return not (self.creates or self.updates or self.archives)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "creates": len(self.creates),
            "updates": len(self.updates) - len(self.reactivations),
            "reactivations": len(self.reactivations),
            "archives": len(self.archives),
            "rejects": len(self.rejects),
            "estimate_rejects": len(self.estimate_rejects),
            "unchanged": len(self.unchanged),
        }


def slug_for(item: ManifestItem) -> str:
    """The catalog slug an item maps to: ``catalog`` when given, else derived from the name."""
    return item.catalog or slugify(item.name)


def _resolve_estimate(item: ManifestItem, currency: str) -> tuple[Money | None, str | None]:
    if item.estimate is None:
        return None, None
    try:
        return item.estimate.to_money(currency), None
    except CurrencyMismatchError as exc:
        return None, f"estimate currency {exc.right} differs from the workspace currency {exc.left}"
    except MoneyParseError as exc:
        return None, f"estimate amount rejected: {exc}"


def _item_changes(item: ManifestItem, state: CatalogState) -> tuple[FieldChange, ...]:
    changes = []
    if state.name != item.name:
        changes.append(FieldChange("name", state.name, item.name))
    if state.kind is not item.kind:
        changes.append(FieldChange("kind", state.kind.value, item.kind.value))
    if state.unit != item.unit:
        changes.append(FieldChange("unit", state.unit, item.unit))
    return tuple(changes)


def plan_sync(ctx: SyncContext) -> SyncPlan:
    """Diff the manifest against what this source created before. Names never identify."""
    manifest = ctx.manifest
    by_external = {req.external_id: req for req in ctx.existing}
    manifest_ids = set(manifest.by_id())
    leaving = {
        req.item_name.lower(): req for req in ctx.existing if req.external_id not in manifest_ids
    }

    creates: list[Create] = []
    updates: list[Update] = []
    rejects: list[Reject] = []
    estimate_rejects: list[Reject] = []
    unchanged: list[str] = []
    # What each slug will look like once this plan applies: (kind, unit, external id that set it).
    # A later item in the same manifest that disagrees is a conflict, never a silent reuse.
    pending: dict[str, tuple[ItemKind, str, str]] = {}

    for item in manifest.items:
        existing = by_external.get(item.id)
        if existing is not None and existing.catalog_item_id in ctx.items_by_id:
            # An id we already know keeps its catalog mapping; its slug is immutable, so a
            # rename changes the item's name, never which item it is.
            state: CatalogState | None = ctx.items_by_id[existing.catalog_item_id]
            slug = state.slug if state is not None else ""
        else:
            try:
                slug = slug_for(item)
            except ValueError as exc:
                rejects.append(
                    Reject(item.id, f"cannot derive a catalog slug from the name: {exc}")
                )
                continue
            state = ctx.catalog.get(slug)
        estimate, estimate_problem = _resolve_estimate(item, ctx.currency)
        if estimate_problem is not None:
            estimate_rejects.append(Reject(item.id, estimate_problem))

        claimed = pending.get(slug)
        if claimed is not None and (claimed[0] is not item.kind or claimed[1] != item.unit):
            rejects.append(
                Reject(
                    item.id,
                    f"kind/unit ({item.kind.value}, {item.unit!r}) conflicts with item {claimed[2]!r} "
                    f"in this manifest, which defines {slug!r} as ({claimed[0].value}, {claimed[1]!r})",
                )
            )
            continue

        item_changes: tuple[FieldChange, ...] = ()
        if state is not None:
            differing = _item_changes(item, state)
            structural = tuple(c for c in differing if c.field in ("kind", "unit"))
            if structural and not state.source_owned:
                what = " and ".join(f"{c.field} {c.old!r} vs {c.new!r}" for c in structural)
                rejects.append(
                    Reject(
                        item.id,
                        f"{what} on catalog item {state.slug!r}, which this source does not own "
                        "(confirmed, merged or referenced elsewhere); resolve it in the catalog",
                    )
                )
                continue
            item_changes = differing if state.source_owned else ()
            if estimate is not None and state.manifest_estimate == estimate:
                estimate = None  # already recorded at this amount by this source
            effective = (item.kind, item.unit) if state.source_owned else (state.kind, state.unit)
        else:
            effective = (item.kind, item.unit)
        pending.setdefault(slug, (effective[0], effective[1], item.id))

        if existing is None:
            rename = leaving.get(item.name.lower())
            creates.append(
                Create(
                    external_id=item.id,
                    item=item,
                    slug=slug,
                    catalog_exists=state is not None,
                    estimate=estimate,
                    likely_rename_of=rename.external_id if rename is not None else None,
                )
            )
            continue

        changes = []
        if existing.qty != item.qty:
            changes.append(FieldChange("qty", existing.qty, item.qty))
        if existing.priority != item.priority:
            changes.append(FieldChange("priority", existing.priority, item.priority))
        reactivate = (
            existing.intent is Intent.ARCHIVED and existing.archive_reason == REMOVED_FROM_SOURCE
        )
        if changes or item_changes or estimate is not None or reactivate:
            updates.append(
                Update(
                    requirement_id=existing.id,
                    external_id=item.id,
                    changes=tuple(changes),
                    item_changes=item_changes,
                    estimate=estimate,
                    reactivate=reactivate,
                )
            )
        else:
            unchanged.append(item.id)

    archives = tuple(
        Archive(requirement_id=req.id, external_id=req.external_id, item_name=req.item_name)
        for req in ctx.existing
        if req.external_id not in manifest_ids and req.intent is not Intent.ARCHIVED
    )
    return SyncPlan(
        project_id=manifest.project_id,
        project_name=manifest.project_name,
        creates=tuple(creates),
        updates=tuple(updates),
        archives=archives,
        rejects=tuple(rejects),
        estimate_rejects=tuple(estimate_rejects),
        unchanged=tuple(unchanged),
    )


def external_ids(entries: Iterable[Create | Update | Archive | Reject]) -> tuple[str, ...]:
    return tuple(entry.external_id for entry in entries)


__all__ = [
    "Archive",
    "CatalogState",
    "Create",
    "ExistingRequirement",
    "FieldChange",
    "Reject",
    "SyncContext",
    "SyncPlan",
    "Update",
    "external_ids",
    "plan_sync",
    "slug_for",
]
