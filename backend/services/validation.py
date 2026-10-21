"""Independent reference + repeatability checks, separate from collection."""
from backend.collector.models import Collection


def structurally_consistent(collection: Collection) -> bool:
    return collection.error is None and collection.profile_id is not None and all(
        r.terminal_page and r.counts_stable and r.reason == "integrity_unverified"
        and r.reported_count == r.recovered_count == len(r.users)
        for r in (collection.followers, collection.following))


def validate_reference(first: Collection, second: Collection, reference: dict) -> bool:
    """Reference must be independently checked by the account owner, same interval.

    Equality of two server responses is insufficient without that reference.
    Never promote only one list if the other fails validation.
    """
    if not structurally_consistent(first) or not structurally_consistent(second):
        return False
    if not (first.profile_id == second.profile_id == reference.get("profile_id")
            and first.username == second.username == reference.get("username")):
        return False
    for kind in ("followers", "following"):
        ids = reference.get(kind + "_ids")
        if (not isinstance(ids, list)
                or any(not isinstance(i, str) or not i.isascii() or not i.isdigit() for i in ids)
                or len(set(ids)) != len(ids)):
            return False
        if not ({u.id for u in getattr(first, kind).users}
                == {u.id for u in getattr(second, kind).users} == set(ids)):
            return False
    for collection in (first, second):
        for result in (collection.followers, collection.following):
            result.state = "complete"
            result.reason = "independent_reference_and_repeat_verified"
            result.integrity_verified = True
    return True
