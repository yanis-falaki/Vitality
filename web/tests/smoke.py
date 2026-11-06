"""Smoke run of the built app in Chromium, against vitality-web serving the fixture model (web spec, §8).

    uv run python tests/serve_fixture.py /tmp/fx --port 4100 &     # in one shell
    <playwright python> tests/smoke.py --url http://127.0.0.1:4100 --shots <folder>

Needs a Python with Playwright and its Chromium (not a dependency of the package). For each view, at
desktop (1366x768), tablet (820x1180) and phone (390x844) sizes: the view renders without console
errors, selecting an item fills the inspector, and the page never scrolls sideways; at desktop size
the Day view does not scroll at all. A screenshot of each view and size (and of the inspector open
on tablet and phone) is saved in `--shots`. Exits 1 on the first failed check.
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

SIZES = {"desktop": (1366, 768), "tablet": (820, 1180), "phone": (390, 844)}


def pick_reader(page: Page) -> None:
    page.locator(".reader-outline .outline-activity").first.click()
    page.locator(".timeline-row").first.click()


def pick_reader_phone(page: Page) -> None:
    page.get_by_role("button", name="☰ Outline").click()
    page.locator(".reader-drawer .outline-activity").first.click()
    page.locator(".timeline-row").first.click()


def pick_graph(page: Page) -> None:
    page.locator(".graph-search input").fill("key-value")
    page.locator(".graph-search input").press("Enter")


def pick_cards(page: Page) -> None:
    page.locator(".card-activity").first.click()


def pick_day(page: Page) -> None:
    page.locator(".day-row").nth(2).click()


VIEWS: list[tuple[str, str, str, Callable[[Page], None]]] = [
    # name, path, a selector that shows the view rendered, how to select something in it
    ("reader", "/", ".page-title", pick_reader),
    ("graph", "/graph", ".graph-stage canvas", pick_graph),
    ("cards", "/cards", ".card-striving", pick_cards),
    ("day", "/day/2026-11-03", ".day-lanes", pick_day),
]


def fail(message: str) -> None:
    print(f"FAIL {message}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:4100")
    parser.add_argument("--shots", type=Path, required=True)
    parser.add_argument("--only", help="comma-separated view names")
    args = parser.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, (width, height) in SIZES.items():
            touch = size != "desktop"
            context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2 if touch else 1,
                                          has_touch=touch, is_mobile=touch)
            for name, path, ready, pick in VIEWS:
                if only and name not in only:
                    continue
                page = context.new_page()
                errors: list[str] = []
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                label = f"{name}/{size}"
                page.goto(args.url + path)
                try:
                    page.wait_for_selector(ready, timeout=15000)
                except Exception:
                    fail(f"{label}: did not render ({ready}); errors: {errors}")
                page.wait_for_timeout(900)
                overflow(page, label)
                if name == "day" and size == "desktop":
                    no_page_scroll(page, label)
                page.screenshot(path=str(args.shots / f"{name}-{size}.png"))
                (pick_reader_phone if name == "reader" and size == "phone" else pick)(page)
                page.wait_for_timeout(900)
                title = page.locator("[data-testid=inspector] .inspector-title")
                if not title.count() or not title.first.is_visible() or not title.first.inner_text().strip():
                    fail(f"{label}: selecting did not fill the inspector")
                overflow(page, label + " (selected)")
                if name == "day" and size == "desktop":
                    no_page_scroll(page, label + " (selected)")
                page.screenshot(path=str(args.shots / f"{name}-{size}-selected.png"))
                if errors:
                    fail(f"{label}: console errors: {errors}")
                print(f"ok   {label}: renders, selects '{title.first.inner_text().strip()[:50]}'")
                page.close()
            context.close()
        browser.close()
    print("smoke: all checks passed")


def overflow(page: Page, label: str) -> None:
    wide = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    if wide[0] > wide[1]:
        fail(f"{label}: the page scrolls sideways ({wide[0]} > {wide[1]} px)")


def no_page_scroll(page: Page, label: str) -> None:
    # Neither the page nor the Day view's left pane may scroll; only the strip and the list do.
    sizes = page.evaluate("""() => {
        const left = document.querySelector('.day-left');
        return [document.documentElement.scrollHeight, window.innerHeight, left.scrollHeight, left.clientHeight];
    }""")
    if sizes[0] > sizes[1] or sizes[2] > sizes[3] + 1:
        fail(f"{label}: the Day view scrolls (page {sizes[0]}/{sizes[1]}, pane {sizes[2]}/{sizes[3]})")


if __name__ == "__main__":
    main()
