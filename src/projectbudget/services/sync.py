"""Sync: pull a manifest from a source, show the plan, apply it in one transaction (ADR-007).

Purchases, inventory and budget data are never touched here. Every run is
recorded. A schema error rejects the file; a same content hash is a no-op; a
semantic conflict rejects only its item.
"""

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import CursorResult, exists, func, select, update
from sqlalchemy.orm import Session

from projectbudget.adapters import manifest as manifest_adapter
from projectbudget.adapters.sources import (
    FetchedManifest,
    GitHubClient,
    GitHubNotFoundError,
    SourceFetchError,
    fetch_file,
    resolve_import_path,
    validate_branch,
    validate_owner_repo,
    validate_repo_path,
)
from projectbudget.domain.catalog import (
    REMOVED_FROM_SOURCE,
    EstimateOrigin,
    Intent,
    ItemKind,
    ReviewState,
    RunStatus,
    Sharing,
    SourceKind,
    validate_slug,
)
from projectbudget.domain.manifest import Manifest, ManifestError, ManifestIssue
from projectbudget.domain.money import Money
from projectbudget.domain.principal import Principal
from projectbudget.domain.sync import (
    CatalogState,
    ExistingRequirement,
    ManualRequirement,
    SyncContext,
    SyncPlan,
    plan_sync,
)
from projectbudget.persistence.models import (
    CatalogItem,
    InventoryMovement,
    PriceEstimate,
    Project,
    PurchaseLine,
    Requirement,
    SyncRun,
    SyncSource,
)
from projectbudget.persistence.session import SessionFactory
from projectbudget.services import workspace
from projectbudget.services.catalog import (
    CatalogItemCreate,
    find_live_by_slug,
    insert_item,
    lock_item,
)
from projectbudget.services.clock import utcnow
from projectbudget.services.guards import NotFoundError, require_write
from projectbudget.services.projects import ProjectNotFoundError

TOKEN_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")
LOCATOR_MAX_LENGTH = 500


class SourceNotFoundError(NotFoundError):
    entity = "Sync source"


class InvalidSourceError(ValueError):
    """A locator, branch, path, project id or token variable name that does not fit the kind."""


class ManifestRejectedError(ValueError):
    """The file failed schema validation; the run was recorded as rejected and nothing applied."""

    def __init__(self, issues: tuple[ManifestIssue, ...], run_id: str) -> None:
        super().__init__("Manifest rejected: " + "; ".join(str(i) for i in issues))
        self.issues = issues
        self.run_id = run_id


class SourceChangedError(ValueError):
    """The source content changed since the preview; look at the new plan before applying."""


class PlanChangedError(ValueError):
    """The workspace changed since the preview, so the plan would differ; preview again first."""


@dataclass(frozen=True, slots=True)
class SourceInfo:
    id: str
    kind: SourceKind
    locator: str
    branch: str | None
    path: str | None
    external_project_id: str | None
    token_env: str | None
    last_revision: str | None
    last_content_hash: str | None
    project_id: str | None
    project_name: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def label(self) -> str:
        if self.kind is SourceKind.GITHUB:
            ref = f"@{self.branch}" if self.branch else ""
            return f"{self.locator}{ref}:{self.path}"
        return self.locator


@dataclass(frozen=True, slots=True)
class SourceCreate:
    kind: SourceKind
    locator: str
    branch: str | None = None
    path: str | None = None
    external_project_id: str | None = None
    token_env: str | None = None
    project_id: str | None = None
    """An existing project to sync into; None creates one from the manifest on first apply."""


@dataclass(frozen=True, slots=True)
class SourceUpdate:
    """The editable settings of a source; kind and locator are its identity and never change.

    Every field is the new whole value: blank clears it.
    """

    branch: str | None = None
    path: str | None = None
    external_project_id: str | None = None
    token_env: str | None = None


@dataclass(frozen=True, slots=True)
class RunInfo:
    id: str
    source_id: str
    status: RunStatus
    revision: str | None
    content_hash: str | None
    summary: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SyncPreview:
    """What a sync would do. ``plan`` is None for a no-op (same content as the last run)."""

    source: SourceInfo
    fetched: FetchedManifest
    manifest: Manifest | None
    plan: SyncPlan | None
    noop: bool
    run: RunInfo | None = None


@dataclass(frozen=True, slots=True)
class SyncResult:
    source: SourceInfo
    run: RunInfo
    plan: SyncPlan | None

    @property
    def noop(self) -> bool:
        return self.run.status is RunStatus.NOOP


# --- sources -------------------------------------------------------------------------------


def _source_info(row: SyncSource, project: Project | None) -> SourceInfo:
    return SourceInfo(
        id=row.id,
        kind=SourceKind(row.kind),
        locator=row.locator,
        branch=row.branch,
        path=row.path,
        external_project_id=row.external_project_id,
        token_env=row.token_env,
        last_revision=row.last_revision,
        last_content_hash=row.last_content_hash,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_info(row: SyncRun) -> RunInfo:
    return RunInfo(
        id=row.id,
        source_id=row.source_id,
        status=RunStatus(row.status),
        revision=row.revision,
        content_hash=row.content_hash,
        summary=json.loads(row.summary) if row.summary else {},
        created_at=row.created_at,
    )


def _load_source(db: Session, source_id: str) -> SyncSource:
    row = db.get(SyncSource, source_id)
    if row is None:
        raise SourceNotFoundError(source_id)
    return row


def _project_of(db: Session, source_id: str) -> Project | None:
    return db.scalar(select(Project).where(Project.source_id == source_id))


def _validate(command: SourceCreate, import_dir: Path) -> SourceCreate:
    kind = SourceKind(command.kind)
    locator = command.locator.strip()
    if not locator or len(locator) > LOCATOR_MAX_LENGTH:
        raise InvalidSourceError("A source needs a locator of at most 500 characters")
    token_env = (command.token_env or "").strip() or None
    external = (command.external_project_id or "").strip() or None
    branch = path = None
    if kind is SourceKind.GITHUB:
        try:
            locator = validate_owner_repo(locator)
            branch = validate_branch(command.branch)
            path = validate_repo_path(command.path)
        except SourceFetchError as exc:
            raise InvalidSourceError(str(exc)) from exc
        if token_env is not None and not TOKEN_ENV_PATTERN.match(token_env):
            raise InvalidSourceError(
                "The token variable must be an environment variable NAME such as GITHUB_TOKEN, never the token"
            )
    else:
        try:
            resolve_import_path(locator, import_dir)
        except SourceFetchError as exc:
            raise InvalidSourceError(str(exc)) from exc
        if token_env is not None:
            raise InvalidSourceError("Only GitHub sources use a token")
        if kind is SourceKind.CSV and external is None:
            raise InvalidSourceError(
                "A CSV source needs the external project id; the file has no project block"
            )
    if external is not None:
        try:
            validate_slug(external)
        except ValueError as exc:
            raise InvalidSourceError(f"External project id: {exc}") from exc
    return SourceCreate(
        kind=kind,
        locator=locator,
        branch=branch,
        path=path,
        external_project_id=external,
        token_env=token_env,
    )


def create_source(
    session_factory: SessionFactory,
    principal: Principal,
    command: SourceCreate,
    *,
    import_dir: Path,
    now: datetime | None = None,
) -> SourceInfo:
    require_write(principal)
    clean = _validate(command, import_dir)
    moment = now or utcnow()
    with session_factory.begin() as db:
        project: Project | None = None
        if command.project_id:
            # Lock first, then check: two attachments to one project must not both pass.
            project = _lock_project(db, command.project_id)
            if project.source_id is not None:
                raise InvalidSourceError(
                    f"Project {project.name!r} is already synced from a source; detach that first"
                )
            if project.archived:
                raise InvalidSourceError(
                    f"Project {project.name!r} is archived; unarchive it first"
                )
        row = SyncSource(
            id=str(uuid.uuid4()),
            kind=clean.kind.value,
            locator=clean.locator,
            branch=clean.branch,
            path=clean.path,
            external_project_id=clean.external_project_id,
            token_env=clean.token_env,
            last_revision=None,
            last_content_hash=None,
            created_at=moment,
            updated_at=moment,
        )
        db.add(row)
        db.flush()
        if project is not None:
            project.source_id = row.id
            project.updated_at = moment
        return _source_info(row, project)


def update_source(
    session_factory: SessionFactory,
    principal: Principal,
    source_id: str,
    command: SourceUpdate,
    *,
    import_dir: Path,
    now: datetime | None = None,
) -> SourceInfo:
    """Change branch, path, token variable or CSV project id in place (#29)."""
    require_write(principal)
    with session_factory.begin() as db:
        row = _load_source(db, source_id)
        clean = _validate(
            SourceCreate(
                kind=SourceKind(row.kind),
                locator=row.locator,
                branch=command.branch,
                path=command.path,
                external_project_id=command.external_project_id,
                token_env=command.token_env,
            ),
            import_dir,
        )
        before = (row.branch, row.path, row.external_project_id)
        row.branch = clean.branch
        row.path = clean.path
        row.external_project_id = clean.external_project_id
        row.token_env = clean.token_env
        if (row.branch, row.path, row.external_project_id) != before:
            # What the source means has changed even if the bytes have not (a CSV project id
            # lives on the source, not in the file): the next preview must plan, not shortcut.
            row.last_content_hash = None
        row.updated_at = now or utcnow()
        db.flush()
        return _source_info(row, _project_of(db, row.id))


def get_source(session_factory: SessionFactory, principal: Principal, source_id: str) -> SourceInfo:
    with session_factory() as db:
        row = _load_source(db, source_id)
        return _source_info(row, _project_of(db, row.id))


def list_sources(session_factory: SessionFactory, principal: Principal) -> list[SourceInfo]:
    with session_factory() as db:
        rows = db.scalars(select(SyncSource).order_by(SyncSource.created_at, SyncSource.id)).all()
        return [_source_info(row, _project_of(db, row.id)) for row in rows]


def detach_source(session_factory: SessionFactory, principal: Principal, source_id: str) -> None:
    """Remove the source; its project and requirements stay and become manual (ADR-007)."""
    require_write(principal)
    with session_factory.begin() as db:
        row = _load_source(db, source_id)
        detach_refs = {"source_id": None, "external_id": None}
        db.execute(update(Project).where(Project.source_id == row.id).values(**detach_refs))
        db.execute(update(Requirement).where(Requirement.source_id == row.id).values(**detach_refs))
        db.execute(
            update(CatalogItem)
            .where(CatalogItem.created_by_source_id == row.id)
            .values(created_by_source_id=None)
        )
        db.delete(row)


def list_runs(
    session_factory: SessionFactory, principal: Principal, source_id: str
) -> list[RunInfo]:
    """Newest first."""
    with session_factory() as db:
        _load_source(db, source_id)
        rows = db.scalars(
            select(SyncRun)
            .where(SyncRun.source_id == source_id)
            .order_by(SyncRun.created_at.desc(), SyncRun.id)
        ).all()
        return [_run_info(row) for row in rows]


# --- fetch and parse --------------------------------------------------------------------------


OPS_GUIDE_HINT = "see docs/operations.md, section GitHub tokens"


def fetch(
    source: SourceInfo,
    *,
    import_dir: Path,
    github: GitHubClient | None = None,
    github_token: str | None = None,
) -> FetchedManifest:
    """Read the source. A GitHub token is read at this moment only and never stored.

    ``github_token`` is the server's default (``PROJECTBUDGET_GITHUB_TOKEN``); a source
    that names its own token variable uses that instead (#30).
    """
    if source.kind is SourceKind.GITHUB:
        if source.token_env:
            token = os.environ.get(source.token_env)
            if not token:
                raise SourceFetchError(
                    f"Environment variable {source.token_env} is not set on the server; "
                    f"set it and restart, or clear the source's token variable to use the "
                    f"server's default token ({OPS_GUIDE_HINT})"
                )
        else:
            token = github_token
        client = github or GitHubClient()
        try:
            return client.fetch(source.locator, branch=source.branch, path=source.path, token=token)
        except GitHubNotFoundError as exc:
            if token is None:
                raise SourceFetchError(
                    f"{exc}. GitHub reports a private repository as not found when the request "
                    "carries no token. For a private repository set PROJECTBUDGET_GITHUB_TOKEN "
                    "on the server, or a token variable on this source, and restart the server "
                    f"({OPS_GUIDE_HINT}). For a public repository, check the owner/repo, branch and path."
                ) from exc
            raise SourceFetchError(
                f"{exc}. The token does not cover this repository (a fine-grained token lists "
                "the repositories it may read), or the owner/repo, branch or path is wrong "
                f"({OPS_GUIDE_HINT})."
            ) from exc
    return fetch_file(source.locator, import_dir)


def parse(source: SourceInfo, fetched: FetchedManifest) -> Manifest:
    if source.kind is SourceKind.CSV:
        return manifest_adapter.parse_csv(
            fetched.content, project_id=source.external_project_id or "project"
        )
    return manifest_adapter.parse_yaml(fetched.content)


# --- plan ------------------------------------------------------------------------------------


def _referenced_elsewhere(db: Session, item_id: str, source_id: str) -> bool:
    probes = (
        select(Requirement.id).where(
            Requirement.catalog_item_id == item_id,
            (Requirement.source_id.is_(None)) | (Requirement.source_id != source_id),
        ),
        select(PurchaseLine.id).where(PurchaseLine.catalog_item_id == item_id),
        select(InventoryMovement.id).where(InventoryMovement.catalog_item_id == item_id),
        select(PriceEstimate.id).where(
            PriceEstimate.catalog_item_id == item_id,
            PriceEstimate.origin != EstimateOrigin.MANIFEST.value,
        ),
    )
    return any(db.scalar(select(exists(probe))) for probe in probes)


def _manifest_estimate(db: Session, item_id: str, currency: str) -> Money | None:
    row = db.scalar(
        select(PriceEstimate)
        .where(
            PriceEstimate.catalog_item_id == item_id,
            PriceEstimate.origin == EstimateOrigin.MANIFEST.value,
        )
        .order_by(
            PriceEstimate.as_of.desc(), PriceEstimate.created_at.desc(), PriceEstimate.id.desc()
        )
        .limit(1)
    )
    return Money(row.amount, currency) if row is not None else None


def _context(
    db: Session, source: SyncSource, manifest: Manifest, currency: str, as_of: date
) -> SyncContext:
    existing_rows = db.execute(
        select(Requirement, CatalogItem.name)
        .join(CatalogItem, CatalogItem.id == Requirement.catalog_item_id)
        .where(Requirement.source_id == source.id)
    ).all()
    existing = tuple(
        ExistingRequirement(
            id=req.id,
            external_id=req.external_id or "",
            catalog_item_id=req.catalog_item_id,
            qty=req.qty,
            priority=req.priority,
            sharing=Sharing(req.sharing),
            intent=Intent(req.intent),
            archive_reason=req.archive_reason,
            item_name=name,
            sort_order=req.sort_order,
        )
        for req, name in existing_rows
    )

    def state_of(row: CatalogItem) -> CatalogState:
        return CatalogState(
            id=row.id,
            slug=row.slug,
            name=row.name,
            kind=ItemKind(row.kind),
            unit=row.unit,
            review_state=ReviewState(row.review_state),
            merged=row.merged_into_id is not None,
            created_by_this_source=row.created_by_source_id == source.id,
            referenced_elsewhere=_referenced_elsewhere(db, row.id, source.id),
            manifest_estimate=_manifest_estimate(db, row.id, currency),
        )

    catalog: dict[str, CatalogState] = {}
    for item in manifest.items:
        try:
            slug = item.catalog or manifest_slug(item.name)
        except ValueError:
            continue
        if slug in catalog:
            continue
        row = find_live_by_slug(db, slug)
        if row is not None:
            catalog[slug] = state_of(row)
    items_by_id: dict[str, CatalogState] = {}
    for req in existing:
        if req.catalog_item_id in items_by_id:
            continue
        row = db.get(CatalogItem, req.catalog_item_id)
        if row is not None:
            items_by_id[req.catalog_item_id] = state_of(row)

    project = _project_of(db, source.id)
    manual: tuple[ManualRequirement, ...] = ()
    same_name: tuple[str, ...] = ()
    if project is not None:
        manual_rows = db.execute(
            select(Requirement, CatalogItem.name)
            .join(CatalogItem, CatalogItem.id == Requirement.catalog_item_id)
            .where(Requirement.project_id == project.id, Requirement.source_id.is_(None))
            .order_by(Requirement.sort_order, Requirement.created_at, Requirement.id)
        ).all()
        manual = tuple(
            ManualRequirement(
                id=req.id,
                catalog_item_id=req.catalog_item_id,
                qty=req.qty,
                priority=req.priority,
                sort_order=req.sort_order,
                item_name=name,
            )
            for req, name in manual_rows
        )
    else:
        same_name = tuple(
            db.scalars(
                select(Project.name)
                .where(
                    func.lower(Project.name) == manifest.project_name.lower(),
                    Project.source_id.is_(None),
                    Project.archived.is_(False),
                )
                .order_by(Project.created_at, Project.id)
            ).all()
        )
    return SyncContext(
        currency=currency,
        manifest=manifest,
        existing=existing,
        catalog=catalog,
        as_of=as_of,
        items_by_id=items_by_id,
        manual=manual,
        attached=project is not None,
        same_name_projects=same_name,
        project_name=project.name if project is not None else None,
        project_external_id=project.external_id if project is not None else None,
    )


def _lock_project(db: Session, project_id: str) -> Project:
    """Serialise attachments to one project (same device as ``catalog.lock_item``)."""
    result = cast(
        CursorResult[Any],
        db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(updated_at=Project.updated_at)
            .execution_options(synchronize_session=False)
        ),
    )
    if result.rowcount == 0:
        raise ProjectNotFoundError(project_id)
    row = db.get(Project, project_id)
    if row is None:  # pragma: no cover - the UPDATE just matched it
        raise ProjectNotFoundError(project_id)
    db.refresh(row)
    return row


def _lock_source(db: Session, source_id: str) -> SyncSource:
    """Serialise applies of one source (same device as ``catalog.lock_item``)."""
    result = cast(
        CursorResult[Any],
        db.execute(
            update(SyncSource)
            .where(SyncSource.id == source_id)
            .values(updated_at=SyncSource.updated_at)
            .execution_options(synchronize_session=False)
        ),
    )
    if result.rowcount == 0:
        raise SourceNotFoundError(source_id)
    row = _load_source(db, source_id)
    db.refresh(row)
    return row


def _lock_affected_items(db: Session, source: SyncSource, manifest: Manifest) -> None:
    """Lock every catalog item the plan may read or write, in id order, before the plan is rebuilt.

    Stock writers (adjust, consume, receive) take the same item lock, so nothing can be
    referenced between the ownership check and the write.
    """
    item_ids = set(
        db.scalars(
            select(Requirement.catalog_item_id).where(Requirement.source_id == source.id)
        ).all()
    )
    for item in manifest.items:
        try:
            slug = item.catalog or manifest_slug(item.name)
        except ValueError:
            continue
        row = find_live_by_slug(db, slug)
        if row is not None:
            item_ids.add(row.id)
    for item_id in sorted(item_ids):
        lock_item(db, item_id)


def manifest_slug(name: str) -> str:
    from projectbudget.domain.catalog import slugify

    return slugify(name)


def _record_run(
    db: Session,
    source: SyncSource,
    status: RunStatus,
    fetched: FetchedManifest,
    summary: dict[str, Any],
    now: datetime,
) -> SyncRun:
    row = SyncRun(
        id=str(uuid.uuid4()),
        source_id=source.id,
        status=status.value,
        revision=fetched.revision,
        content_hash=fetched.content_hash,
        summary=json.dumps(summary, sort_keys=True),
        created_at=now,
    )
    db.add(row)
    db.flush()
    return row


def preview(
    session_factory: SessionFactory,
    principal: Principal,
    source_id: str,
    *,
    import_dir: Path,
    fetched: FetchedManifest | None = None,
    force: bool = False,
    github: GitHubClient | None = None,
    github_token: str | None = None,
    now: datetime | None = None,
) -> SyncPreview:
    """Fetch, parse and diff without writing anything but the run record for no-ops and rejects."""
    moment = now or utcnow()
    source = get_source(session_factory, principal, source_id)
    content = fetched or fetch(
        source, import_dir=import_dir, github=github, github_token=github_token
    )
    currency = workspace.get_currency(session_factory)
    if not force and content.content_hash == source.last_content_hash:
        with session_factory.begin() as db:
            run = _record_run(
                db,
                _load_source(db, source_id),
                RunStatus.NOOP,
                content,
                {"reason": "same content"},
                moment,
            )
            info = _run_info(run)
        return SyncPreview(
            source=source, fetched=content, manifest=None, plan=None, noop=True, run=info
        )
    try:
        manifest = parse(source, content)
    except ManifestError as exc:
        with session_factory.begin() as db:
            run = _record_run(
                db,
                _load_source(db, source_id),
                RunStatus.REJECTED,
                content,
                {"issues": [str(issue) for issue in exc.issues]},
                moment,
            )
            run_id = run.id
        raise ManifestRejectedError(exc.issues, run_id) from exc
    with session_factory() as db:
        plan = plan_sync(
            _context(db, _load_source(db, source_id), manifest, currency, content.as_of)
        )
    return SyncPreview(source=source, fetched=content, manifest=manifest, plan=plan, noop=False)


# --- apply -----------------------------------------------------------------------------------


def _apply(
    db: Session,
    source: SyncSource,
    manifest: Manifest,
    plan: SyncPlan,
    currency: str,
    as_of: date,
    now: datetime,
) -> None:
    project = _project_of(db, source.id)
    if project is None:
        project = Project(
            id=str(uuid.uuid4()),
            name=manifest.project_name,
            archived=False,
            notes="",
            source_id=source.id,
            external_id=manifest.project_id,
            created_at=now,
            updated_at=now,
        )
        db.add(project)
        db.flush()
    elif project.name != manifest.project_name or project.external_id != manifest.project_id:
        project.name = manifest.project_name
        project.external_id = manifest.project_id
        project.updated_at = now

    def add_estimate(item_id: str, price: Money) -> None:
        db.add(
            PriceEstimate(
                id=str(uuid.uuid4()),
                catalog_item_id=item_id,
                amount=price.amount,
                currency=price.currency,
                as_of=as_of,
                confidence="medium",
                origin=EstimateOrigin.MANIFEST.value,
                created_at=now,
            )
        )

    for create in plan.creates:
        row = find_live_by_slug(db, create.slug)
        if row is None:
            row = insert_item(
                db,
                CatalogItemCreate(
                    name=create.item.name,
                    kind=create.item.kind,
                    unit=create.item.unit,
                    slug=create.slug,
                    review_state=ReviewState.UNREVIEWED,
                ),
                now,
            )
            row.created_by_source_id = source.id
        db.add(
            Requirement(
                id=str(uuid.uuid4()),
                project_id=project.id,
                catalog_item_id=row.id,
                qty=create.item.qty,
                priority=create.item.priority,
                sort_order=create.sort_order,
                intent=Intent.ACTIVE.value,
                archive_reason=None,
                sharing=create.item.sharing.value,
                notes=create.item.notes,
                source_id=source.id,
                external_id=create.external_id,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        if create.estimate is not None:
            add_estimate(row.id, create.estimate)

    for adopt in plan.adoptions:
        req = db.get(Requirement, adopt.requirement_id)
        if req is None:
            continue
        req.source_id = source.id
        req.external_id = adopt.external_id
        for change in adopt.changes:
            setattr(req, change.field, change.new)
        req.updated_at = now
        if adopt.estimate is not None:
            add_estimate(req.catalog_item_id, adopt.estimate)

    for edit in plan.updates:
        req = db.get(Requirement, edit.requirement_id)
        if req is None:
            continue
        for change in edit.changes:
            setattr(req, change.field, change.new)
        if edit.reactivate:
            req.intent = Intent.ACTIVE.value
            req.archive_reason = None
        req.updated_at = now
        if edit.item_changes:
            item = db.get(CatalogItem, req.catalog_item_id)
            if item is not None:
                for change in edit.item_changes:
                    setattr(item, change.field, change.new)
                item.updated_at = now
        if edit.estimate is not None:
            add_estimate(req.catalog_item_id, edit.estimate)

    for archive in plan.archives:
        req = db.get(Requirement, archive.requirement_id)
        if req is not None:
            req.intent = Intent.ARCHIVED.value
            req.archive_reason = REMOVED_FROM_SOURCE
            req.updated_at = now
    db.flush()


def apply(
    session_factory: SessionFactory,
    principal: Principal,
    source_id: str,
    *,
    import_dir: Path,
    fetched: FetchedManifest | None = None,
    expected_content_hash: str | None = None,
    expected_plan_digest: str | None = None,
    force: bool = False,
    github: GitHubClient | None = None,
    github_token: str | None = None,
    now: datetime | None = None,
) -> SyncResult:
    """Fetch, plan and apply in one transaction; the run is recorded as applied, noop or failed.

    ``expected_content_hash`` and ``expected_plan_digest`` come from the preview a person
    approved: apply refuses when the content or the resulting plan is no longer that one.
    """
    require_write(principal)
    moment = now or utcnow()
    shown = preview(
        session_factory,
        principal,
        source_id,
        import_dir=import_dir,
        fetched=fetched,
        force=force,
        github=github,
        github_token=github_token,
        now=moment,
    )
    if expected_content_hash is not None and shown.fetched.content_hash != expected_content_hash:
        raise SourceChangedError("The source changed since the preview; review the new plan first")
    if shown.noop or shown.plan is None or shown.manifest is None:
        if shown.run is None:  # a no-op preview always records its run
            raise RuntimeError("No-op preview without a run record")
        return SyncResult(source=shown.source, run=shown.run, plan=None)
    currency = workspace.get_currency(session_factory)
    try:
        with session_factory.begin() as db:
            # Lock the source and every item involved, then rebuild the plan from the state no
            # one else can change any more. A plan computed before this transaction is only a
            # preview; if a human approved that preview, it must still be the plan we apply.
            source = _lock_source(db, source_id)
            _lock_affected_items(db, source, shown.manifest)
            plan = plan_sync(_context(db, source, shown.manifest, currency, shown.fetched.as_of))
            if expected_plan_digest is not None and plan.digest != expected_plan_digest:
                raise PlanChangedError(
                    "The workspace changed since the preview and the plan would differ; preview again"
                )
            _apply(db, source, shown.manifest, plan, currency, shown.fetched.as_of, moment)
            source.last_revision = shown.fetched.revision
            source.last_content_hash = shown.fetched.content_hash
            source.updated_at = moment
            summary = {**plan.counts, "revision": shown.fetched.revision}
            run = _record_run(db, source, RunStatus.APPLIED, shown.fetched, summary, moment)
            info = _run_info(run)
            source_info = _source_info(source, _project_of(db, source.id))
    except Exception as exc:
        with session_factory.begin() as db:
            _record_run(
                db,
                _load_source(db, source_id),
                RunStatus.FAILED,
                shown.fetched,
                {"error": str(exc)[:500]},
                moment,
            )
        raise
    return SyncResult(source=source_info, run=info, plan=plan)
