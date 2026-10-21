from backend.collector.models import ListResult, User


class InsufficientData(ValueError):
    pass


def non_followers(followers: ListResult, following: ListResult) -> list[User]:
    """Only compare verified snapshots. Names are labels, never identity keys."""
    for result in (followers, following):
        ids = [u.id for u in result.users]
        if (result.state != "complete" or not result.integrity_verified
                or not result.terminal_page or not result.counts_stable
                or any(not isinstance(i, str) or not i.isascii() or not i.isdigit() for i in ids)
                or len(set(ids)) != len(ids)
                or type(result.reported_count) is not int
                or result.reported_count != len(ids)):
            raise InsufficientData("Both lists must have verified integrity and completeness")
    follower_ids = {u.id for u in followers.users}
    return sorted((u for u in following.users if u.id not in follower_ids),
                  key=lambda u: (u.username.casefold(), u.id))
