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
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class ManualRequirement:
    """A requirement entered by hand in the project this source is attached to (ADR-012)."""

    id: str
    catalog_item_id: str
    qty: int
    priority: int
    sort_order: int
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
    references: tuple[str, ...] = ()
    """What else uses the item, in words: a requirement entered by hand or from another source,
    a purchase, stock movements, a price the owner entered. Empty when nothing does."""
    manifest_estimate: Money | None = None
    """The latest estimate this source already recorded for the item."""
    unit_locked: bool = False
    """The catalog refuses a unit change: requirements, prices, stock or purchases are in it
    (``services.catalog.unit_in_use``), this source's own included."""

    @property
    def referenced_elsewhere(self) -> bool:
        return bool(self.references)

    @property
    def ownership_reasons(self) -> tuple[str, ...]:
        """Why the manifest may not change kind or unit, each as a clause; empty when it may."""
        reasons = []
        if not self.created_by_this_source:
            reasons.append("this source did not create it")
        if self.merged:
            reasons.append("it was merged into another item")
        if self.review_state is ReviewState.CONFIRMED:
            reasons.append("you confirmed it in the catalog")
        if self.references:
            reasons.append("it is used by " + " and ".join(self.references))
        return tuple(reasons)

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
    manual: tuple[ManualRequirement, ...] = ()
    """Hand-entered requirements of the attached project, in project order; none when unattached."""
    attached: bool = False
    """The source already has a project (chosen on creation, or created by an earlier apply)."""
    project_name: str | None = None
    """The attached project's current name; the manifest renames it when they differ."""
    project_external_id: str | None = None
    """The attached project's current external id (manifest project id, or CSV source setting)."""
    same_name_projects: tuple[str, ...] = ()
    """Names of unsynced projects that share the manifest's project name; only read when unattached."""


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
    sort_order: int = 0
    """The item's position in the file: the manifest's order is the purchase order (ADR-012)."""


@dataclass(frozen=True, slots=True)
class Update:
    requirement_id: str
    external_id: str
    changes: tuple[FieldChange, ...]
    """Requirement fields: qty, priority, sort_order (the item's position in the file)."""
    item_changes: tuple[FieldChange, ...]
    """Catalog item fields the source owns here: name, kind, unit."""
    estimate: Money | None
    """A new estimate to record, or None when nothing changed."""
    reactivate: bool = False
    """The requirement was archived as removed_from_source and comes back."""


@dataclass(frozen=True, slots=True)
class Adopt:
    """A hand-entered requirement for the same catalog item becomes this source's (ADR-012).

    It gains the external id and the source takes qty, priority and order; intent, sharing,
    notes and consumption history stay as they are.
    """

    requirement_id: str
    external_id: str
    item_name: str
    changes: tuple[FieldChange, ...]
    """Requirement fields the manifest sets on adoption: qty, priority, sort_order."""
    estimate: Money | None


@dataclass(frozen=True, slots=True)
class Archive:
    requirement_id: str
    external_id: str
    item_name: str


@dataclass(frozen=True, slots=True)
class Reject:
    external_id: str
    reason: str
    """The whole story in one sentence, for the API and the CLI."""
    item_id: str | None = None
    item_name: str | None = None
    mismatch: str | None = None
    """For a kind/unit reject: what the manifest says against what the catalog has."""
    because: str | None = None
    """For a kind/unit reject: the real reason the manifest may not change the item."""
    fix: str | None = None
    """For a kind/unit reject: the two ways out, as a sentence."""


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
    adoptions: tuple[Adopt, ...] = ()
    """Manual requirements of the attached project that this apply takes over."""
    warnings: tuple[str, ...] = ()
    """Things worth knowing before applying; they change nothing by themselves."""
    project_changes: tuple[FieldChange, ...] = ()
    """Name or external id the attached project takes from the manifest (or the CSV setting)."""

    @property
    def reactivations(self) -> tuple[Update, ...]:
        return tuple(u for u in self.updates if u.reactivate)

    @property
    def shared_reject_cause(self) -> str | None:
        """When several rejects have one and the same cause, that cause; otherwise None."""
        causes = {r.because for r in self.rejects}
        if len(self.rejects) > 1 and len(causes) == 1 and None not in causes:
            return causes.pop()
        return None

    @property
    def digest(self) -> str:
        """A fingerprint of exactly what this plan would do; apply checks it against the preview."""
        return hashlib.sha256(repr(self).encode("utf-8")).hexdigest()

    @property
    def empty(self) -> bool:
        """Nothing to write: applying would change no row."""
        return not (
            self.creates or self.updates or self.archives or self.adoptions or self.project_changes
        )

    @property
    def counts(self) -> dict[str, int]:
        return {
            "creates": len(self.creates),
            "adoptions": len(self.adoptions),
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


def _ownership_reject(
    external_id: str, state: CatalogState, structural: tuple[FieldChange, ...]
) -> Reject:
    """A kind/unit mismatch on an item the source does not own: say why and what to do (#28)."""
    mismatch = "; ".join(
        f"{c.field} {c.new!r} in the manifest, {c.old!r} in the catalog" for c in structural
    )
    because = " and ".join(state.ownership_reasons) or "it is not this source's to change"
    fixes = []
    for c in structural:
        if c.field == "unit" and state.unit_locked:
            # The catalog refuses this change too (UnitInUseError), so only one way out.
            fixes.append(
                f"Set unit: {c.old} in the manifest; the item's unit cannot change on its page "
                f"while requirements, prices, stock or purchases are in {c.old!r}."
            )
        else:
            fixes.append(
                f"Either change the item's {c.field} to {c.new!r} on its page, "
                f"or set {c.field}: {c.old} in the manifest."
            )
    fix = " ".join(fixes)
    return Reject(
        external_id,
        f"{state.name}: {mismatch}; this source may not change it because {because}. {fix}",
        item_id=state.id,
        item_name=state.name,
        mismatch=mismatch,
        because=because,
        fix=fix,
    )


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
    adoptions: list[Adopt] = []
    updates: list[Update] = []
    rejects: list[Reject] = []
    estimate_rejects: list[Reject] = []
    unchanged: list[str] = []
    # Hand-entered requirements per catalog item, in project order. Each manifest item that
    # maps to the item adopts the next unused one; whatever is left stays manual.
    manual_by_item: dict[str, list[ManualRequirement]] = {}
    for req in ctx.manual:
        manual_by_item.setdefault(req.catalog_item_id, []).append(req)
    # What each slug will look like once this plan applies: (kind, unit, external id that set it).
    # A later item in the same manifest that disagrees is a conflict, never a silent reuse.
    pending: dict[str, tuple[ItemKind, str, str]] = {}

    for position, item in enumerate(manifest.items):
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
                rejects.append(_ownership_reject(item.id, state, structural))
                continue
            item_changes = differing if state.source_owned else ()
            if estimate is not None and state.manifest_estimate == estimate:
                estimate = None  # already recorded at this amount by this source
            effective = (item.kind, item.unit) if state.source_owned else (state.kind, state.unit)
        else:
            effective = (item.kind, item.unit)
        pending.setdefault(slug, (effective[0], effective[1], item.id))

        if existing is None:
            unused = manual_by_item.get(state.id) if state is not None else None
            if unused:
                manual = unused.pop(0)
                taken = []
                if manual.qty != item.qty:
                    taken.append(FieldChange("qty", manual.qty, item.qty))
                if manual.priority != item.priority:
                    taken.append(FieldChange("priority", manual.priority, item.priority))
                if manual.sort_order != position:
                    taken.append(FieldChange("sort_order", manual.sort_order, position))
                adoptions.append(
                    Adopt(
                        requirement_id=manual.id,
                        external_id=item.id,
                        item_name=manual.item_name,
                        changes=tuple(taken),
                        estimate=estimate,
                    )
                )
                continue
            rename = leaving.get(item.name.lower())
            creates.append(
                Create(
                    external_id=item.id,
                    item=item,
                    slug=slug,
                    catalog_exists=state is not None,
                    estimate=estimate,
                    likely_rename_of=rename.external_id if rename is not None else None,
                    sort_order=position,
                )
            )
            continue

        changes = []
        if existing.qty != item.qty:
            changes.append(FieldChange("qty", existing.qty, item.qty))
        if existing.priority != item.priority:
            changes.append(FieldChange("priority", existing.priority, item.priority))
        if existing.sort_order != position:
            changes.append(FieldChange("sort_order", existing.sort_order, position))
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
    project_changes: list[FieldChange] = []
    if ctx.attached:
        if ctx.project_name != manifest.project_name:
            project_changes.append(FieldChange("name", ctx.project_name, manifest.project_name))
        if ctx.project_external_id != manifest.project_id:
            project_changes.append(
                FieldChange("external_id", ctx.project_external_id, manifest.project_id)
            )
    warnings: list[str] = []
    if not ctx.attached and ctx.same_name_projects:
        names = ", ".join(repr(name) for name in ctx.same_name_projects)
        warnings.append(
            f"A project named {names} already exists and is not synced. Applying creates a new "
            f"project {manifest.project_name!r} beside it. To sync the existing project instead, "
            "detach this source and add it again with that project chosen."
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
        adoptions=tuple(adoptions),
        warnings=tuple(warnings),
        project_changes=tuple(project_changes),
    )


def external_ids(
    entries: Iterable[Create | Adopt | Update | Archive | Reject],
) -> tuple[str, ...]:
    return tuple(entry.external_id for entry in entries)


__all__ = [
    "Adopt",
    "Archive",
    "CatalogState",
    "Create",
    "ExistingRequirement",
    "FieldChange",
    "ManualRequirement",
    "Reject",
    "SyncContext",
    "SyncPlan",
    "Update",
    "external_ids",
    "plan_sync",
    "slug_for",
]
