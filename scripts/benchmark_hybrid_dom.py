"""Offline Chromium benchmark; local fictitious rows, all network blocked."""
import json
import statistics
import time

from backend.experimental.playwright_browser import (
    ROOT, extract_visible_people, scroll_geometry, scroll_metrics_and_advance,
    refuse_if_gate, refuse_if_gate_batched,
)


def main():
    from playwright.sync_api import sync_playwright
    samples = {"previous": [], "combined": []}
    guards = {"previous": [], "combined": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            rows = "".join(f'<a href="/fixture_{i}/" style="display:block;height:40px">'
                           f'fixture_{i}</a>' for i in range(511))
            page.set_content('<div role="dialog"><div style="height:309px;overflow-y:auto">'
                             + rows + '</div></div>')
            dialog = page.get_by_role("dialog")
            # Interleave the two paths and discard warmup, with identical contents.
            for iteration in range(110):
                for method in ("previous", "combined"):
                    started = time.perf_counter()
                    if method == "previous":
                        scroll_geometry(dialog)
                        scroll_metrics_and_advance(dialog)
                        snapshot = extract_visible_people(dialog)
                        scroll_geometry(dialog)
                    else:
                        scroll_metrics_and_advance(dialog)
                        snapshot = extract_visible_people(dialog, include_geometry=True)
                    seconds = time.perf_counter() - started
                    assert len(snapshot["rows"]) == 511
                    if iteration >= 10:
                        samples[method].append(seconds)
            for _ in range(20):
                for method, guard in (("previous", refuse_if_gate), ("combined", refuse_if_gate_batched)):
                    started = time.perf_counter()
                    guard(page)
                    guards[method].append(time.perf_counter() - started)
        finally:
            browser.close()
    result = {"mode": "offline_local_html", "network": "blocked", "rows": 511,
              "samples_per_path": 100,
              "mean_ms": {key: round(statistics.mean(values) * 1000, 3)
                          for key, values in samples.items()},
              "median_ms": {key: round(statistics.median(values) * 1000, 3)
                            for key, values in samples.items()},
              "includes_network_or_scroll_pause": False}
    result["guard_samples_per_path"] = 20
    result["guard_mean_ms"] = {key: round(statistics.mean(values) * 1000, 3)
                               for key, values in guards.items()}
    output = ROOT / "reports" / "hybrid-collector" / "offline-dom-benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
