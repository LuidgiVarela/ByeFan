"""Evidence and results; unknown counts are None, never zero."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class User:
    id: str
    username: str


@dataclass
class ListResult:
    users: list[User] = field(default_factory=list)
    reported_count: int | None = None
    state: str = "unavailable"
    reason: str = "not_attempted"
    pages: int = 0
    terminal_page: bool = False
    counts_stable: bool = False
    integrity_verified: bool = False

    @property
    def recovered_count(self) -> int:
        return len({u.id for u in self.users})


@dataclass
class Collection:
    username: str
    started_at: str
    profile_id: str | None = None
    followers: ListResult = field(default_factory=ListResult)
    following: ListResult = field(default_factory=ListResult)
    elapsed_seconds: float = 0
    error: str | None = None
    requests: list[dict] = field(default_factory=list)
