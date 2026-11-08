"""Who is acting. Every service function receives one (ADR-005).

In V1 the principal is always the single owner, authenticated either by a
browser session or by an API token. The type exists now so that authorization
has exactly one place to live when multi-user arrives.
"""

from dataclasses import dataclass
from typing import Literal

AuthMethod = Literal["session", "token"]


@dataclass(frozen=True, slots=True)
class Principal:
    owner_id: str
    username: str
    auth: AuthMethod
    read_only: bool = False

    @property
    def can_write(self) -> bool:
        return not self.read_only
