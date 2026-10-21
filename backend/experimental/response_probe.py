"""Summarize JSON responses the browser already received; never issue requests."""
import json
from urllib.parse import urlsplit


def candidate_profile_count(document, max_nodes=20000):
    """Schema hint only: objects with handle plus numeric id/pk may be unrelated."""
    stack = [document]
    visited = 0
    candidates = 0
    while stack and visited < max_nodes:
        value = stack.pop()
        visited += 1
        if isinstance(value, dict):
            username = value.get("username")
            identifier = value.get("id", value.get("pk"))
            if isinstance(username, str) and isinstance(identifier, (str, int)) \
                    and str(identifier).isascii() and str(identifier).isdigit():
                candidates += 1
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return candidates, bool(stack)


class PageResponseProbe:
    """Bounded in-memory diagnostic, with no URLs, bodies, tokens or names in reports."""

    def __init__(self, page, max_responses_per_list=30, max_body_bytes=2_000_000):
        self.phase = None
        self.pending = {"followers": [], "following": []}
        self.overflow = {"followers": 0, "following": 0}
        self.max_responses_per_list = max_responses_per_list
        self.max_body_bytes = max_body_bytes
        # Body is available at requestfinished; no request is sent by this probe.
        page.on("requestfinished", self.observe_request)

    def set_phase(self, direction):
        self.phase = direction

    def observe_request(self, request):
        response = request.response()
        if response is not None:
            self.observe(response)

    def observe(self, response):
        direction = self.phase
        if direction not in self.pending:
            return
        host = (urlsplit(response.url).hostname or "").lower()
        if host != "instagram.com" and not host.endswith(".instagram.com"):
            return
        if response.request.resource_type not in ("xhr", "fetch"):
            return
        if "json" not in response.headers.get("content-type", "").lower():
            return
        if len(self.pending[direction]) >= self.max_responses_per_list:
            self.overflow[direction] += 1
            return
        self.pending[direction].append(response)

    def summary(self):
        report = {}
        for direction, responses in self.pending.items():
            item = {"json_responses": len(responses), "overflow": self.overflow[direction],
                    "candidate_profile_objects": 0, "body_skipped_or_unreadable": 0,
                    "non_200_responses": 0, "schema_truncated": 0}
            for response in responses:
                if response.status != 200:
                    item["non_200_responses"] += 1
                    continue
                try:
                    body = response.body()
                    if len(body) > self.max_body_bytes:
                        item["body_skipped_or_unreadable"] += 1
                        continue
                    candidates, truncated = candidate_profile_count(json.loads(body))
                    item["candidate_profile_objects"] += candidates
                    item["schema_truncated"] += int(truncated)
                except Exception:
                    # Playwright can raise its own Error type; never log a body/URL.
                    item["body_skipped_or_unreadable"] += 1
            report[direction] = item
        return report
