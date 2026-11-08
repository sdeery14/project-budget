"""Checks every service function shares: who may write, and what exists.

Authorization lives in the service layer so routers, the CLI and the sync
adapters cannot bypass it (ADR-005). Read-only API tokens are stopped here even
if a router forgot to.
"""

from projectbudget.domain.principal import Principal


class ReadOnlyPrincipalError(PermissionError):
    """A read-only API token tried to change state."""

    def __init__(self) -> None:
        super().__init__("This API token is read-only")


class NotFoundError(LookupError):
    """No row with that id belongs to this workspace."""

    entity = "Record"

    def __init__(self, record_id: str) -> None:
        super().__init__(f"{self.entity} {record_id!r} not found")
        self.record_id = record_id


def require_write(principal: Principal) -> None:
    if not principal.can_write:
        raise ReadOnlyPrincipalError
