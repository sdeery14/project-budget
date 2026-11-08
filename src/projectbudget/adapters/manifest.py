"""Parse manifest v1 from YAML or CSV with line-level schema errors (RFC v3 §6, ADR-007).

A schema error (invalid YAML, a missing field, a duplicate id, a float where a
decimal string belongs) rejects the whole file: ``ManifestError`` carries every
issue with its line. Semantic questions (does the catalog item exist, does the
currency match) are not asked here; the sync engine answers them per item.
"""

import csv
import io
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from projectbudget.domain.catalog import (
    NAME_MAX_LENGTH,
    SLUG_MAX_LENGTH,
    UNIT_MAX_LENGTH,
    ItemKind,
    Sharing,
    validate_slug,
)
from projectbudget.domain.manifest import (
    AMOUNT_PATTERN,
    MANIFEST_VERSION,
    Manifest,
    ManifestError,
    ManifestEstimate,
    ManifestIssue,
    ManifestItem,
)

CSV_COLUMNS = ("id", "name", "qty", "unit", "kind", "priority", "sharing", "catalog", "estimate")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024

Slug = Annotated[StrictStr, Field(min_length=1, max_length=SLUG_MAX_LENGTH)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EstimateDocument(Strict):
    amount: Annotated[StrictStr, Field(pattern=AMOUNT_PATTERN)] | None = Field(
        default=None, description='Decimal string, e.g. "12.50". YAML numbers are rejected.'
    )
    amount_minor: StrictInt | None = Field(default=None, ge=0)
    currency: Annotated[StrictStr, Field(pattern=r"^[A-Za-z]{3}$")] | None = None


class ItemDocument(Strict):
    id: Slug
    name: Annotated[StrictStr, Field(min_length=1, max_length=NAME_MAX_LENGTH)]
    qty: Annotated[StrictInt, Field(ge=1)]
    unit: Annotated[StrictStr, Field(min_length=1, max_length=UNIT_MAX_LENGTH)] = "each"
    kind: ItemKind = ItemKind.CONSUMABLE
    priority: Annotated[StrictInt, Field(ge=1, le=3)] = 2
    sharing: Sharing = Sharing.SHARED
    estimate: EstimateDocument | None = None
    catalog: Slug | None = None
    notes: StrictStr = ""


class ProjectDocument(Strict):
    id: Slug
    name: Annotated[StrictStr, Field(min_length=1, max_length=NAME_MAX_LENGTH)] | None = None


class ManifestDocument(Strict):
    """Manifest v1. The JSON Schema in the repository is generated from this model."""

    version: Annotated[StrictInt, Field(ge=MANIFEST_VERSION, le=MANIFEST_VERSION)]
    project: ProjectDocument
    items: list[ItemDocument]


def manifest_json_schema() -> dict[str, Any]:
    schema = ManifestDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://github.com/sdeery14/project-budget/blob/main/docs/manifest.schema.json"
    schema["title"] = "ProjectBudget manifest v1"
    return schema


# --- YAML ------------------------------------------------------------------------------


def _line_map(node: yaml.Node, path: str, lines: dict[str, int]) -> None:
    """Record the 1-based line of every mapping value and sequence element by its path."""
    lines.setdefault(path, node.start_mark.line + 1)
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            key = str(getattr(key_node, "value", "?"))
            child = f"{path}.{key}" if path else key
            lines[child] = value_node.start_mark.line + 1
            _line_map(value_node, child, lines)
    elif isinstance(node, yaml.SequenceNode):
        for index, element in enumerate(node.value):
            child = f"{path}[{index}]"
            lines[child] = element.start_mark.line + 1
            _line_map(element, child, lines)


def _path_of(loc: tuple[int | str, ...]) -> str:
    path = ""
    for part in loc:
        path = (
            f"{path}[{part}]"
            if isinstance(part, int)
            else (f"{path}.{part}" if path else str(part))
        )
    return path


def _nearest_line(path: str, lines: dict[str, int]) -> int | None:
    while path:
        if path in lines:
            return lines[path]
        cut = max(path.rfind("."), path.rfind("["))
        path = path[:cut] if cut > 0 else ""
    return lines.get("")


def _issues_from_validation(
    exc: ValidationError, lines: dict[str, int]
) -> tuple[ManifestIssue, ...]:
    issues = []
    for error in exc.errors():
        path = _path_of(tuple(error["loc"]))
        message = error["msg"]
        if error["type"] == "string_type" and path.endswith("amount"):
            message = 'amount must be a quoted decimal string such as "12.50", not a number'
        issues.append(
            ManifestIssue(path=path or "document", message=message, line=_nearest_line(path, lines))
        )
    return tuple(issues)


def _raw_id_issues(data: dict[str, Any], lines: dict[str, int]) -> list[ManifestIssue]:
    """Slug and duplicate problems found on the raw document, so they show up with schema errors."""
    issues: list[ManifestIssue] = []
    items = data.get("items")
    if not isinstance(items, list):
        return issues
    seen: dict[str, int] = {}
    for index, raw in enumerate(items):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        item_id = raw["id"]
        path = f"items[{index}].id"
        try:
            validate_slug(item_id)
        except ValueError as exc:
            issues.append(ManifestIssue(path=path, message=str(exc), line=lines.get(path)))
        if item_id in seen:
            issues.append(
                ManifestIssue(
                    path=path,
                    message=f"duplicate item id {item_id!r} (first used at items[{seen[item_id]}])",
                    line=lines.get(path),
                )
            )
        seen.setdefault(item_id, index)
    return issues


def _validate(data: dict[str, Any], lines: dict[str, int]) -> ManifestDocument:
    """Schema validation plus the id checks, reported together with their lines."""
    try:
        return ManifestDocument.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(
            _issues_from_validation(exc, lines) + tuple(_raw_id_issues(data, lines))
        ) from exc


def _to_domain(doc: ManifestDocument, lines: dict[str, int]) -> Manifest:
    issues: list[ManifestIssue] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(doc.items):
        path = f"items[{index}].id"
        try:
            validate_slug(item.id)
        except ValueError as exc:
            issues.append(ManifestIssue(path=path, message=str(exc), line=lines.get(path)))
        if item.id in seen:
            issues.append(
                ManifestIssue(
                    path=path,
                    message=f"duplicate item id {item.id!r} (first used at items[{seen[item.id]}])",
                    line=lines.get(path),
                )
            )
        seen.setdefault(item.id, index)
        if item.catalog is not None:
            try:
                validate_slug(item.catalog)
            except ValueError as exc:
                cpath = f"items[{index}].catalog"
                issues.append(ManifestIssue(path=cpath, message=str(exc), line=lines.get(cpath)))
        if (
            item.estimate is not None
            and item.estimate.amount is None
            and item.estimate.amount_minor is None
        ):
            epath = f"items[{index}].estimate"
            issues.append(
                ManifestIssue(
                    path=epath,
                    message="estimate needs amount or amount_minor",
                    line=lines.get(epath),
                )
            )
    try:
        validate_slug(doc.project.id)
    except ValueError as exc:
        issues.append(
            ManifestIssue(path="project.id", message=str(exc), line=lines.get("project.id"))
        )
    if issues:
        raise ManifestError(tuple(issues))
    return Manifest(
        version=doc.version,
        project_id=doc.project.id,
        project_name=doc.project.name or doc.project.id,
        items=tuple(
            ManifestItem(
                id=item.id,
                name=" ".join(item.name.split()),
                qty=item.qty,
                unit=item.unit.strip().lower(),
                kind=item.kind,
                priority=item.priority,
                sharing=item.sharing,
                estimate=ManifestEstimate(
                    amount=item.estimate.amount,
                    amount_minor=item.estimate.amount_minor,
                    currency=item.estimate.currency,
                )
                if item.estimate is not None
                else None,
                catalog=item.catalog,
                notes=item.notes.strip(),
            )
            for item in doc.items
        ),
    )


def _text(content: bytes | str) -> str:
    if isinstance(content, str):
        return content
    if len(content) > MAX_MANIFEST_BYTES:
        raise ManifestError((ManifestIssue(path="document", message="manifest larger than 2 MB"),))
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError((ManifestIssue(path="document", message=f"not UTF-8: {exc}"),)) from exc


def parse_yaml(content: bytes | str) -> Manifest:
    """Parse a ``project-budget.yaml``; every schema problem is reported with its line."""
    text = _text(content)
    try:
        loader = yaml.SafeLoader(text)
        try:
            node = loader.get_single_node()
            data = loader.construct_document(node) if node is not None else None
        finally:
            loader.dispose()
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        problem = getattr(exc, "problem", None) or str(exc)
        raise ManifestError(
            (ManifestIssue(path="document", message=f"invalid YAML: {problem}", line=line),)
        ) from exc
    if not isinstance(data, dict):
        raise ManifestError(
            (ManifestIssue(path="document", message="the manifest must be a mapping", line=1),)
        )
    lines: dict[str, int] = {}
    if node is not None:
        _line_map(node, "", lines)
    return _to_domain(_validate(data, lines), lines)


# --- CSV -------------------------------------------------------------------------------


def parse_csv(
    content: bytes | str, *, project_id: str, project_name: str | None = None
) -> Manifest:
    """Parse the fixed-column CSV form. The project id comes from the source, not the file."""
    text = _text(content)
    reader = csv.DictReader(io.StringIO(text))
    header = tuple(name.strip() for name in (reader.fieldnames or ()))
    if set(header) != set(CSV_COLUMNS) or len(header) != len(CSV_COLUMNS):
        raise ManifestError(
            (
                ManifestIssue(
                    path="header",
                    message=f"columns must be exactly {', '.join(CSV_COLUMNS)}; got {', '.join(header) or 'nothing'}",
                    line=1,
                ),
            )
        )
    items: list[dict[str, Any]] = []
    lines: dict[str, int] = {"project.id": 1}
    for index, row in enumerate(reader):
        line = index + 2
        if all(not (value or "").strip() for value in row.values()):
            continue
        record: dict[str, Any] = {}
        clean = {
            key.strip(): (value or "").strip() for key, value in row.items() if key is not None
        }
        for key in ("id", "name", "unit", "kind", "sharing", "catalog"):
            if clean.get(key):
                record[key] = clean[key]
        for key in ("qty", "priority"):
            if clean.get(key):
                try:
                    record[key] = int(clean[key])
                except ValueError:
                    record[key] = clean[key]  # let validation report it
        if clean.get("estimate"):
            record["estimate"] = {"amount": clean["estimate"]}
        items.append(record)
        position = len(items) - 1
        lines[f"items[{position}]"] = line
        for key in record:
            lines[f"items[{position}].{key}"] = line
        lines[f"items[{position}].estimate.amount"] = line
    data = {
        "version": MANIFEST_VERSION,
        "project": {"id": project_id, "name": project_name or project_id},
        "items": items,
    }
    return _to_domain(_validate(data, lines), lines)
