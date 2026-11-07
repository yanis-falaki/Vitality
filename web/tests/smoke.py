"""Smoke run of the built app in Chromium, against vitality-web serving the fixture model (web spec, §8, §10).

    uv run python tests/serve_fixture.py /tmp/fx --port 4100 &     # in one shell
    <playwright python> tests/smoke.py --url http://127.0.0.1:4100 --shots <folder>

    uv run python tests/serve_fixture.py /tmp/fx-large --port 4101 --large &
    <playwright python> tests/smoke.py --url http://127.0.0.1:4101 --shots <folder> --large

Needs a Python with Playwright and its Chromium (not a dependency of the package). For each view, at
desktop (1366x768), tablet (820x1180) and phone (390x844) sizes: the view renders without console
errors, selecting an item fills the inspector, and the page never scrolls sideways; at desktop size
the Day view does not scroll at all, and in the Graph, after zooming and panning, clicking a node
animates the camera from where it was (never through the full view, the same renderer kept). A screenshot of each view and size (and of the inspector open
on tablet and phone) is saved in `--shots`. Exits 1 on the first failed check.

With `--large` (the large fixture, about four months), also: the Day view's arrows and calendar move
between days (at every size); the Reader's heatmap renders and opens a day; the Graph's time range
switches; and "+ Evidence" over all of it keeps the page responsive (the worst gap between frames
while arranging stays under 100 ms) and either finishes or says there is too much evidence. Times
are printed.
"""

import argparse
import re
import sys
import time
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


# -- the large fixture's checks -----------------------------------------------------------------

def check_day_navigation(page: Page, label: str) -> None:
    # The arrows step between days with actions; the calendar jumps to a day of an earlier month.
    start = page.url
    page.locator(".day-step").first.click()
    page.wait_for_function("u => location.href !== u", arg=start)
    stepped = page.url
    page.locator(".calendar-button").click()
    page.locator(".calendar").wait_for()
    page.get_by_role("button", name="Previous month").click()
    page.get_by_role("button", name="Previous month").click()
    target = page.locator("button.calendar-day").first
    picked = target.get_attribute("data-day")
    target.click()
    page.wait_for_function("d => location.pathname === '/day/' + d", arg=picked)
    if page.locator(".calendar").count():
        fail(f"{label}: the calendar stayed open after a pick")
    heading = page.locator(".day-head h1").inner_text()
    print(f"ok   {label}: arrows to {stepped.rsplit('/', 1)[-1][:10]}, calendar to {picked} ({heading})")


def check_heatmap(page: Page, label: str) -> None:
    cells = page.locator(".heat button.heat-cell")
    if cells.count() < 20:
        fail(f"{label}: the heatmap shows {cells.count()} days with actions")
    box = page.locator(".heat-scroll").evaluate("e => [e.scrollWidth, e.clientWidth, e.getBoundingClientRect().right]")
    if box[2] > page.viewport_size["width"]:  # type: ignore[index]
        fail(f"{label}: the heatmap overflows the page")
    count, first = cells.count(), cells.last
    day = re.search(r"\w+, (\w+ \d+)", first.get_attribute("title") or "")
    first.click()
    page.wait_for_url(re.compile(r"/day/\d{4}-\d\d-\d\d"))
    print(f"ok   {label}: heatmap of {count} days ({box[0]}/{box[1]} px wide), opens {day and day.group(1)}")
    page.go_back()


# Frame gaps until the "Arranging" note goes or the "too much evidence" note comes (the frame that then
# mounts the new graph is not counted: that is drawing, not arranging).
FRAMES = """() => { window.__gaps = []; let last = performance.now(); window.__done = false;
  const note = () => document.querySelector('.graph-arranging:not(.graph-note)');
  const watch = new MutationObserver(() => {
    if ((window.__seen && !note()) || document.querySelector('.graph-note')) { window.__done = true; watch.disconnect(); }
    if (note()) window.__seen = true; });
  window.__seen = false; watch.observe(document.body, { subtree: true, childList: true });
  const tick = (t) => { if (!window.__done) window.__gaps.push(t - last); last = t; if (!window.__stop) requestAnimationFrame(tick); };
  window.__stop = false; requestAnimationFrame(tick); }"""


def arrange(page: Page, click: Callable[[], None]) -> tuple[float, float, str]:
    # Seconds until arranged (or the note), the worst frame gap meanwhile (ms), and how it ended.
    page.evaluate(FRAMES)
    start = time.perf_counter()
    click()
    page.wait_for_timeout(50)
    page.wait_for_function("() => !document.querySelector('.graph-arranging:not(.graph-note)') || document.querySelector('.graph-note')",
                           timeout=60000)
    seconds = time.perf_counter() - start
    worst = page.evaluate("() => { window.__stop = true; return Math.max(0, ...window.__gaps.slice(1)); }")
    ended = "note" if page.locator(".graph-note").count() else "arranged"
    return seconds, worst, ended


def check_graph(page: Page, label: str) -> None:
    legend = lambda: page.locator(".graph-legend").inner_text().replace("\n", ", ")  # noqa: E731
    page.wait_for_function("() => !document.querySelector('.graph-arranging')", timeout=60000)
    print(f"ok   {label}: all: {legend()}")
    seconds, worst, _ = arrange(page, lambda: page.get_by_role("button", name=re.compile("7 days")).click())
    print(f"ok   {label}: last 7 days arranged in {seconds:.1f} s (worst frame gap {worst:.0f} ms): {legend()}")
    seconds, worst, ended = arrange(page, lambda: page.get_by_role("button", name="+ Evidence").click())
    print(f"ok   {label}: + evidence, 7 days: {ended} in {seconds:.1f} s (worst frame gap {worst:.0f} ms): {legend()}")
    seconds, worst, ended = arrange(page, lambda: page.get_by_role("button", name=re.compile("30 days")).click())
    if worst > 100:
        fail(f"{label}: + evidence, 30 days: a frame took {worst:.0f} ms while arranging")
    print(f"ok   {label}: + evidence, 30 days: {ended} in {seconds:.1f} s (worst frame gap {worst:.0f} ms): {legend()}")
    page.screenshot(path=str(SHOTS / f"graph-{label.split('/')[1]}-evidence-30.png"))
    seconds, worst, ended = arrange(page, lambda: page.get_by_role("button", name="All").click())
    if worst > 100:
        fail(f"{label}: + evidence, all: a frame took {worst:.0f} ms while arranging")
    if ended == "note":
        page.locator(".graph-note").wait_for()
    print(f"ok   {label}: + evidence, all: {ended} in {seconds:.1f} s (worst frame gap {worst:.0f} ms): {legend()}")
    page.screenshot(path=str(SHOTS / f"graph-{label.split('/')[1]}-evidence-all.png"))


# Zoom and pan, then click a node: the camera must go from where it is to the node, never through the
# full view (a renderer made anew starts there), and the renderer must stay the same one.
CAMERA = """() => { const box = document.querySelector('[data-testid=graph]'); const r = box.sigma;
  window.__renderer = r; window.__cams = []; window.__stop = false;
  const tick = () => { const s = box.sigma.getCamera().getState(); window.__cams.push([s.x, s.y, s.ratio]);
    if (!window.__stop) requestAnimationFrame(tick); };
  requestAnimationFrame(tick); }"""


def check_graph_camera(page: Page, label: str) -> None:
    page.wait_for_function("() => !document.querySelector('.graph-arranging')", timeout=60000)
    page.wait_for_function("() => document.querySelector('[data-testid=graph]').sigma")
    box = page.locator("[data-testid=graph]").bounding_box()
    assert box is not None
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    for _ in range(3):
        page.mouse.wheel(0, -240)
        page.wait_for_timeout(150)
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx - 120, cy - 60, steps=8)
    page.mouse.up()
    page.wait_for_timeout(500)
    # A node on screen, away from the edges, to click.
    at = page.evaluate("""([w, h]) => { const r = document.querySelector('[data-testid=graph]').sigma; const g = r.getGraph();
      for (const kind of ['activity', 'striving', 'action']) for (const n of g.nodes()) {
        if (g.getNodeAttribute(n, 'kind') !== kind) continue;
        const p = r.graphToViewport(g.getNodeAttributes(n));
        if (p.x > 60 && p.x < w - 60 && p.y > 60 && p.y < h - 60) return [n, p.x, p.y]; }
      return null; }""", [box["width"], box["height"]])
    if at is None:
        fail(f"{label}: no node on screen after zooming and panning")
    before = page.evaluate("() => { const s = document.querySelector('[data-testid=graph]').sigma.getCamera().getState(); return [s.x, s.y, s.ratio]; }")
    page.evaluate(CAMERA)
    page.mouse.click(box["x"] + at[1], box["y"] + at[2])
    page.wait_for_function("n => new URLSearchParams(location.search).get('sel') === n", arg=at[0])
    page.wait_for_timeout(900)
    cams, same = page.evaluate("""() => { window.__stop = true;
      return [window.__cams, window.__renderer === document.querySelector('[data-testid=graph]').sigma]; }""")
    if not same:
        fail(f"{label}: selecting a node made a new renderer")
    reset = [c for c in cams if abs(c[0] - 0.5) < 1e-6 and abs(c[1] - 0.5) < 1e-6 and abs(c[2] - 1) < 1e-6]
    if reset:
        fail(f"{label}: the camera went back to the full view while selecting")
    first = cams[0]
    if max(abs(first[0] - before[0]), abs(first[1] - before[1]), abs(first[2] - before[2])) > 1e-3:
        fail(f"{label}: the camera jumped before animating ({before} -> {first})")
    print(f"ok   {label}: selecting '{at[0]}' animates from the camera where it was "
          f"(ratio {before[2]:.2f} -> {cams[-1][2]:.2f}, {len(cams)} frames)")


SHOTS = Path(".")


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
    parser.add_argument("--large", action="store_true", help="the large fixture: also its checks (see above)")
    args = parser.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)
    global SHOTS
    SHOTS = args.shots
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
                if args.large and name == "day":
                    path = "/day"
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
                if name == "graph" and size == "desktop":
                    check_graph_camera(page, label)
                    page.goto(args.url + path)
                    page.wait_for_selector(ready, timeout=15000)
                if args.large:
                    if name == "day":
                        check_day_navigation(page, label)
                        page.screenshot(path=str(args.shots / f"{name}-{size}-earlier.png"))
                        page.locator(".calendar-button").click()
                        page.screenshot(path=str(args.shots / f"{name}-{size}-calendar.png"))
                        page.keyboard.press("Escape")
                    if name == "graph" and size == "desktop":
                        check_graph(page, label)
                        page.get_by_role("button", name="Model").click()
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
                if args.large and name == "reader":
                    close = page.locator(".inspector-close button")
                    if close.count() and close.first.is_visible():
                        close.first.click()
                    check_heatmap(page, label)
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
