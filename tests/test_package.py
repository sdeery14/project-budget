"""The empty package imports and reports a version.

PR2 adds the database fixtures; until then this keeps the CI matrix exercising
both jobs on a non-empty test suite.
"""

import os

import projectbudget


def test_version_is_a_non_empty_string() -> None:
    assert isinstance(projectbudget.__version__, str)
    assert projectbudget.__version__


def test_ci_sets_a_test_database_url_or_runs_locally() -> None:
    url = os.environ.get("PROJECTBUDGET_TEST_DATABASE_URL")
    if os.environ.get("CI"):
        assert url, "CI jobs must set PROJECTBUDGET_TEST_DATABASE_URL (ADR-001: both backends)"
    if url:
        assert url.startswith(("sqlite", "postgresql"))
