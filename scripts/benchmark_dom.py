"""Offline DOM benchmark; uses generated HTML only, never Instagram."""
import json
import statistics
import time

from playwright.sync_api import sync_playwright

from backend.experimental.playwright_browser import extract_visible_people


def legacy_visible_people(dialog):
    rows = []
    anchors = dialog.locator("a[href]")
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        if anchor.is_visible():
            href = anchor.get_attribute("href") or ""
            if href.startswith("/fixture_"):
                rows.append(href)
    return rows


def main():
    links = "".join(f'<a href="/fixture_{i}/">fixture_{i}</a>' for i in range(350))
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            page = instance.new_page()
            page.set_content('<div role="dialog">' + links + '</div>')
            dialog = page.get_by_role("dialog")
            measurements = {"legacy": [], "batched": []}
            for _ in range(3):
                started = time.perf_counter()
                old_rows = legacy_visible_people(dialog)
                measurements["legacy"].append(time.perf_counter() - started)
                started = time.perf_counter()
                new_rows = extract_visible_people(dialog)["rows"]
                measurements["batched"].append(time.perf_counter() - started)
                if len(old_rows) != len(new_rows):
                    raise RuntimeError("benchmark_results_mismatch")
        finally:
            instance.close()
    old = statistics.median(measurements["legacy"])
    new = statistics.median(measurements["batched"])
    print(json.dumps({"fixture_anchors": 350, "repetitions": 3,
                      "legacy_median_seconds": round(old, 3),
                      "batched_median_seconds": round(new, 3),
                      "speedup": round(old / new, 1)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
