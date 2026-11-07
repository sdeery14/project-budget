"""ProjectBudget: one honest shopping backlog across all your projects."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("projectbudget")
except PackageNotFoundError:  # pragma: no cover - source tree that was never installed
    __version__ = "0.0.0"

__all__ = ["__version__"]
