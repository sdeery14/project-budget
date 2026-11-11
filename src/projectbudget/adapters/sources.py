"""Fetch a manifest from one of the three sources (RFC v3 §6, ADR-007).

- Local file or CSV: a path under the configured import directory; revision is
  the SHA-256 of the content and the estimate date is the file's mtime.
- GitHub: ``owner/repo`` plus branch and path through the Contents API; revision
  is the latest commit SHA touching the file and the estimate date is that
  commit's date. Only ``api.github.com`` is ever contacted; there is no URL
  input, which closes the SSRF question without an allowlist engine.

The HTTP getter is injectable so tests never touch the network.
"""

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from projectbudget.domain.manifest import DEFAULT_MANIFEST_PATH

GITHUB_API_HOST = "api.github.com"
GITHUB_API = f"https://{GITHUB_API_HOST}"
GITHUB_TIMEOUT_SECONDS = 20
MAX_FETCH_BYTES = 2 * 1024 * 1024
OWNER_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")

Getter = Callable[[str, dict[str, str]], bytes]


class SourceFetchError(Exception):
    """The source could not be read: missing file, path outside the import directory, HTTP error."""


class GitHubNotFoundError(SourceFetchError):
    """GitHub answered 404: a wrong locator, or a private repository fetched without a token."""


@dataclass(frozen=True, slots=True)
class FetchedManifest:
    content: bytes
    revision: str
    """Commit SHA for GitHub, content hash for files."""
    as_of: date
    """The date manifest estimates are recorded with: commit date or file mtime."""
    content_hash: str
    description: str

    @classmethod
    def of(
        cls, content: bytes, *, revision: str | None, as_of: date, description: str
    ) -> "FetchedManifest":
        digest = content_hash(content)
        return cls(
            content=content,
            revision=revision or digest,
            as_of=as_of,
            content_hash=digest,
            description=description,
        )


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# --- local files ---------------------------------------------------------------------------


def resolve_import_path(locator: str, import_dir: Path) -> Path:
    """The file a locator names, refusing anything that escapes the import directory."""
    if not locator.strip():
        raise SourceFetchError("The file path is empty")
    normalised = locator.replace("\\", "/")
    candidate = PurePosixPath(normalised)
    windows = PureWindowsPath(normalised)  # a drive letter or UNC root escapes on Windows
    if candidate.is_absolute() or windows.drive or windows.root or ".." in candidate.parts:
        raise SourceFetchError(
            f"Path {locator!r} must be relative to the import directory and not climb out of it"
        )
    root = import_dir.expanduser().resolve()
    target = (root / Path(*candidate.parts)).resolve()
    if root != target and root not in target.parents:
        raise SourceFetchError(f"Path {locator!r} is outside the import directory {root}")
    return target


def fetch_file(locator: str, import_dir: Path) -> FetchedManifest:
    target = resolve_import_path(locator, import_dir)
    try:
        if target.stat().st_size > MAX_FETCH_BYTES:
            raise SourceFetchError(f"{locator!r} is larger than 2 MB")
        content = target.read_bytes()
        mtime = datetime.fromtimestamp(target.stat().st_mtime, tz=UTC).date()
    except FileNotFoundError as exc:
        raise SourceFetchError(
            f"No file {locator!r} under the import directory {import_dir}"
        ) from exc
    except OSError as exc:
        raise SourceFetchError(f"Cannot read {locator!r}: {exc}") from exc
    return FetchedManifest.of(content, revision=None, as_of=mtime, description=f"file {locator}")


# --- GitHub ----------------------------------------------------------------------------------


def _https_opener() -> urllib.request.OpenerDirector:
    """An opener that speaks HTTPS and nothing else: no file:, ftp:, data: or redirects off-host."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def default_getter(url: str, headers: dict[str, str]) -> bytes:
    """GET over HTTPS from api.github.com and nowhere else."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != GITHUB_API_HOST:
        raise SourceFetchError(
            f"Refusing to contact {parts.hostname!r}; only {GITHUB_API_HOST} is allowed"
        )
    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310  (host checked above)
    try:
        with _https_opener().open(request, timeout=GITHUB_TIMEOUT_SECONDS) as response:
            return bytes(response.read(MAX_FETCH_BYTES + 1))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GitHubNotFoundError(f"GitHub answered 404 for {parts.path}") from exc
        raise SourceFetchError(f"GitHub answered {exc.code} for {parts.path}") from exc
    except urllib.error.URLError as exc:
        raise SourceFetchError(f"Cannot reach GitHub: {exc.reason}") from exc


def validate_owner_repo(locator: str) -> str:
    clean = locator.strip().strip("/")
    if not OWNER_REPO.match(clean) or clean.startswith(".") or "/." in clean:
        raise SourceFetchError(f"GitHub locator must look like owner/repo, got {locator!r}")
    return clean


def validate_repo_path(path: str | None) -> str:
    clean = (path or DEFAULT_MANIFEST_PATH).strip().strip("/")
    parts = PurePosixPath(clean).parts
    if not clean or ".." in parts or any(not part for part in parts):
        raise SourceFetchError(f"Repository path {path!r} is not a plain relative file path")
    return clean


def validate_branch(branch: str | None) -> str | None:
    if branch is None or not branch.strip():
        return None
    clean = branch.strip()
    if not BRANCH.match(clean) or ".." in clean:
        raise SourceFetchError(f"Branch {branch!r} is not a valid reference name")
    return clean


class GitHubClient:
    """The two Contents-API calls sync needs, behind an injectable HTTP getter."""

    def __init__(self, get: Getter | None = None) -> None:
        self._get = get or default_getter

    def _json(self, url: str, token: str | None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "projectbudget",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        raw = self._get(url, headers)
        if len(raw) > MAX_FETCH_BYTES:
            raise SourceFetchError("GitHub response larger than 2 MB")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SourceFetchError("GitHub returned something that is not JSON") from exc

    def fetch(
        self, locator: str, *, branch: str | None, path: str | None, token: str | None
    ) -> FetchedManifest:
        owner_repo = validate_owner_repo(locator)
        repo_path = validate_repo_path(path)
        ref = validate_branch(branch)
        quoted_path = urllib.parse.quote(repo_path)
        query: dict[str, str] = {"path": repo_path, "per_page": "1"}
        if ref:
            query["sha"] = ref
        commits = self._json(
            f"{GITHUB_API}/repos/{owner_repo}/commits?{urllib.parse.urlencode(query)}", token
        )
        if not isinstance(commits, list) or not commits:
            raise SourceFetchError(
                f"No commits touch {repo_path} in {owner_repo}" + (f"@{ref}" if ref else "")
            )
        head = commits[0]
        try:
            sha = str(head["sha"])
            when = str(head["commit"]["committer"]["date"])
        except (KeyError, TypeError) as exc:
            raise SourceFetchError("Unexpected commit shape from GitHub") from exc
        as_of = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone(UTC).date()
        contents = self._json(
            f"{GITHUB_API}/repos/{owner_repo}/contents/{quoted_path}?ref={urllib.parse.quote(sha)}",
            token,
        )
        if not isinstance(contents, dict) or contents.get("encoding") != "base64":
            raise SourceFetchError(f"{repo_path} in {owner_repo} is not a file")
        try:
            content = base64.b64decode(str(contents.get("content", "")), validate=False)
        except ValueError as exc:
            raise SourceFetchError("GitHub returned undecodable content") from exc
        return FetchedManifest.of(
            content,
            revision=sha,
            as_of=as_of,
            description=f"github {owner_repo}@{sha[:7]} {repo_path}",
        )
