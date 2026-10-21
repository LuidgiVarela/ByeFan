"""Offline-only assessment of documented Instagram follow-list response shapes.

This module has no transport, credentials, endpoint URLs, or live execution path.
It does not claim that a terminal cursor proves list completeness.
"""

from dataclasses import dataclass
from typing import Any, Literal

Direction = Literal["followers", "following"]
Format = Literal["private_graphql", "private_rest_v1"]


@dataclass(frozen=True)
class Identity:
    id: str
    username: str


@dataclass(frozen=True)
class Page:
    rows: tuple[Identity, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class Assessment:
    rows: tuple[Identity, ...]
    pages: int
    observed_rows: int
    duplicate_ids: int
    terminal_observed: bool
    count_matches: bool | None
    state: Literal["terminal_unverified", "incomplete", "invalid"]
    reason: str
    integrity_verified: bool = False


class InvalidPage(ValueError):
    """Unexpected or contradictory response shape; do not publish a result."""


def _identity(raw: Any) -> Identity:
    if not isinstance(raw, dict):
        raise InvalidPage("user_not_object")
    raw_id = raw.get("pk_id") or raw.get("pk") or raw.get("id")
    username = raw.get("username")
    if (isinstance(raw_id, bool) or not isinstance(raw_id, (str, int))
            or not str(raw_id).isdigit()):
        raise InvalidPage("stable_id_missing")
    if (not isinstance(username, str) or not username
            or len(username) > 30
            or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789._"
                   for c in username.lower())):
        raise InvalidPage("invalid_username")
    return Identity(str(raw_id), username.lower())


def parse_page(payload: Any, fmt: Format, direction: Direction) -> Page:
    """Parse only private mobile GQL and private REST v1 documented list envelopes."""
    if fmt not in ("private_graphql", "private_rest_v1"):
        raise InvalidPage("unknown_format")
    if direction not in ("followers", "following"):
        raise InvalidPage("unknown_direction")
    if not isinstance(payload, dict) or payload.get("status") not in (None, "ok"):
        raise InvalidPage("response_not_ok")
    if fmt == "private_graphql":
        data = payload.get("data")
        key = f"xdt_api__v1__friendships__{direction}"
        if not isinstance(data, dict) or not isinstance(data.get(key), dict):
            raise InvalidPage("graphql_root_missing")
        body = data[key]
    else:
        body = payload
    users = body.get("users")
    if not isinstance(users, list):
        raise InvalidPage("users_missing")
    cursor = body.get("next_max_id")
    if cursor is not None and (not isinstance(cursor, (str, int)) or isinstance(cursor, bool)):
        raise InvalidPage("invalid_cursor")
    if cursor == "":
        cursor = None
    return Page(tuple(_identity(user) for user in users),
                str(cursor) if cursor is not None else None)


def assess_pages(
    payloads: list[Any], fmt: Format, direction: Direction,
    reported_count: int | None = None,
    *, stopped_early: bool = False,
) -> Assessment:
    """Check offline pagination and identity consistency; never certify integrity.

    Payloads must be consecutive pages from one crawl. A missing next cursor is
    only a terminal signal. It cannot prove that the platform sent every user.
    """
    if reported_count is not None and (isinstance(reported_count, bool)
                                       or not isinstance(reported_count, int)
                                       or reported_count < 0):
        raise ValueError("reported_count must be a nonnegative integer or None")
    seen: dict[str, Identity] = {}
    usernames: dict[str, str] = {}
    cursors: set[str] = set()
    observed = duplicates = 0
    reason = "no_pages"
    terminal = False
    try:
        for index, payload in enumerate(payloads):
            page = parse_page(payload, fmt, direction)
            observed += len(page.rows)
            for row in page.rows:
                old = seen.get(row.id)
                if old is not None:
                    duplicates += 1
                    if old.username != row.username:
                        raise InvalidPage("id_username_conflict")
                elif row.username in usernames and usernames[row.username] != row.id:
                    raise InvalidPage("username_id_conflict")
                else:
                    seen[row.id] = row
                    usernames[row.username] = row.id
            if page.next_cursor is None:
                if index != len(payloads) - 1:
                    raise InvalidPage("pages_after_terminal")
                terminal = True
                reason = "cursor_exhausted_unverified"
            elif page.next_cursor in cursors:
                reason = "repeated_cursor"
                break
            else:
                cursors.add(page.next_cursor)
                reason = "pagination_not_finished"
    except InvalidPage as exc:
        return Assessment(tuple(seen.values()), len(payloads), observed, duplicates,
                          False, None, "invalid", str(exc))
    rows = tuple(seen.values())
    count_matches = len(rows) == reported_count if reported_count is not None else None
    if stopped_early:
        terminal = False
        reason = "stopped_early"
    elif terminal and count_matches is False:
        reason = "terminal_count_mismatch"
    state = "terminal_unverified" if terminal and count_matches is not False else "incomplete"
    return Assessment(rows, len(payloads), observed, duplicates, terminal,
                      count_matches, state, reason)
