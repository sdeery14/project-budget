"""Fetching manifests: files under the import directory only, GitHub through the Contents API only."""

import base64
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from projectbudget import localtime
from projectbudget.adapters import sources
from projectbudget.adapters.sources import GitHubClient, GitHubNotFoundError, SourceFetchError


def test_fetch_file_hashes_content_and_dates_estimates_by_mtime(tmp_path: Path) -> None:
    target = tmp_path / "shed" / "project-budget.yaml"
    target.parent.mkdir()
    target.write_bytes(b"version: 1\n")
    stamp = datetime(2026, 10, 20, 12, 0, tzinfo=UTC).timestamp()
    os.utime(target, (stamp, stamp))
    fetched = sources.fetch_file("shed/project-budget.yaml", tmp_path)
    assert fetched.content == b"version: 1\n"
    assert fetched.revision == fetched.content_hash == sources.content_hash(b"version: 1\n")
    assert fetched.as_of == date(2026, 10, 20)
    assert fetched.description == "file shed/project-budget.yaml"
    # Windows-style separators are accepted and normalised.
    assert (
        sources.fetch_file("shed\\project-budget.yaml", tmp_path).content_hash
        == fetched.content_hash
    )


@pytest.mark.parametrize(
    "locator", ["../secrets.yaml", "/etc/passwd", "C:/Windows/win.ini", "a/../../b", "", "   "]
)
def test_paths_outside_the_import_directory_are_refused(tmp_path: Path, locator: str) -> None:
    with pytest.raises(SourceFetchError):
        sources.resolve_import_path(locator, tmp_path)


def test_missing_and_oversized_files(tmp_path: Path) -> None:
    with pytest.raises(SourceFetchError, match="No file"):
        sources.fetch_file("nope.yaml", tmp_path)
    big = tmp_path / "big.yaml"
    big.write_bytes(b"x" * (sources.MAX_FETCH_BYTES + 1))
    with pytest.raises(SourceFetchError, match="2 MB"):
        sources.fetch_file("big.yaml", tmp_path)


class FakeGitHub:
    """Answers the two Contents-API calls and records every request."""

    def __init__(
        self,
        content: bytes = b"version: 1\n",
        sha: str = "abc123def456",
        when: str = "2026-10-05T14:03:00Z",
    ) -> None:
        self.content = content
        self.sha = sha
        self.when = when
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> bytes:
        self.calls.append((url, headers))
        if "/commits?" in url:
            return json.dumps(
                [{"sha": self.sha, "commit": {"committer": {"date": self.when}}}]
            ).encode()
        if "/contents/" in url:
            return json.dumps(
                {"encoding": "base64", "content": base64.b64encode(self.content).decode("ascii")}
            ).encode()
        raise AssertionError(url)


def test_github_fetch_uses_commit_sha_and_commit_date() -> None:
    fake = FakeGitHub()
    fetched = GitHubClient(fake).fetch(
        "someone/some-repo", branch="main", path="deep/project-budget.yaml", token=None
    )
    assert fetched.content == b"version: 1\n"
    assert fetched.revision == "abc123def456"
    assert fetched.as_of == date(2026, 10, 5)
    assert fetched.description == "github someone/some-repo@abc123d deep/project-budget.yaml"
    commits_url, commits_headers = fake.calls[0]
    assert commits_url.startswith("https://api.github.com/repos/someone/some-repo/commits?")
    assert "path=deep%2Fproject-budget.yaml" in commits_url
    assert "sha=main" in commits_url
    assert "Authorization" not in commits_headers
    assert commits_headers["Accept"] == "application/vnd.github+json"
    contents_url, _ = fake.calls[1]
    assert (
        contents_url
        == "https://api.github.com/repos/someone/some-repo/contents/deep/project-budget.yaml?ref=abc123def456"
    )


def test_github_token_goes_in_the_bearer_header_and_defaults_apply() -> None:
    fake = FakeGitHub()
    GitHubClient(fake).fetch("someone/some-repo", branch=None, path=None, token="ghp_secret")
    url, headers = fake.calls[0]
    assert headers["Authorization"] == "Bearer ghp_secret"
    assert "sha=" not in url
    assert "path=project-budget.yaml" in url


def test_github_locator_branch_and_path_are_validated() -> None:
    client = GitHubClient(FakeGitHub())
    for locator in ("not-a-repo", "a/b/c", "../x/y", ".hidden/repo", "owner/../repo"):
        with pytest.raises(SourceFetchError):
            client.fetch(locator, branch=None, path=None, token=None)
    with pytest.raises(SourceFetchError, match="Branch"):
        client.fetch("o/r", branch="bad branch", path=None, token=None)
    with pytest.raises(SourceFetchError, match="path"):
        client.fetch("o/r", branch=None, path="../etc/passwd", token=None)


def test_github_answers_that_are_not_what_we_expect() -> None:
    def no_commits(url: str, headers: dict[str, str]) -> bytes:
        return b"[]"

    with pytest.raises(SourceFetchError, match="No commits"):
        GitHubClient(no_commits).fetch("o/r", branch=None, path=None, token=None)

    def not_json(url: str, headers: dict[str, str]) -> bytes:
        return b"<html>"

    with pytest.raises(SourceFetchError, match="not JSON"):
        GitHubClient(not_json).fetch("o/r", branch=None, path=None, token=None)

    class Directory(FakeGitHub):
        def __call__(self, url: str, headers: dict[str, str]) -> bytes:
            if "/contents/" in url:
                return b"[]"
            return super().__call__(url, headers)

    with pytest.raises(SourceFetchError, match="not a file"):
        GitHubClient(Directory()).fetch("o/r", branch=None, path=None, token=None)


def test_a_404_from_github_is_its_own_error_other_statuses_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub says 404 for a private repository without a token; the service explains it (#29)."""
    import urllib.error

    class Answering:
        def __init__(self, code: int) -> None:
            self.code = code

        def open(self, request: object, timeout: float) -> object:
            raise urllib.error.HTTPError("https://api.github.com/x", self.code, "nope", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(sources, "_https_opener", lambda: Answering(404))
    with pytest.raises(GitHubNotFoundError, match="404 for /repos/o/r/commits"):
        sources.default_getter("https://api.github.com/repos/o/r/commits?per_page=1", {})
    monkeypatch.setattr(sources, "_https_opener", lambda: Answering(403))
    with pytest.raises(SourceFetchError, match="403") as caught:
        sources.default_getter("https://api.github.com/repos/o/r/commits?per_page=1", {})
    assert not isinstance(caught.value, GitHubNotFoundError)


def test_default_getter_only_talks_to_api_github_com() -> None:
    for url in (
        "http://api.github.com/x",
        "https://raw.githubusercontent.com/x",
        "https://evil.example/api.github.com",
    ):
        with pytest.raises(SourceFetchError, match=r"only api.github.com"):
            sources.default_getter(url, {})


def test_derived_dates_are_the_owners_date_not_the_utc_date(tmp_path: Path) -> None:
    """#27: a file saved or a commit made at 19:04 in New York is dated that day, not tomorrow."""
    localtime.configure("America/New_York")
    try:
        target = tmp_path / "project-budget.yaml"
        target.write_bytes(b"version: 1\n")
        stamp = datetime(2026, 11, 10, 0, 4, tzinfo=UTC).timestamp()
        os.utime(target, (stamp, stamp))
        assert sources.fetch_file("project-budget.yaml", tmp_path).as_of == date(2026, 11, 9)
        fetched = GitHubClient(FakeGitHub(when="2026-11-10T00:04:00Z")).fetch(
            "someone/some-repo", branch=None, path="project-budget.yaml", token=None
        )
        assert fetched.as_of == date(2026, 11, 9)
    finally:
        localtime.configure(None)
