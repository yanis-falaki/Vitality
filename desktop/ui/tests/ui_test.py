"""The interface against the fake core (mock.html), in a headless browser.

Review Focus 5 and the Setup rules: Setup on the first run and while an essential check fails
(opened at the failing step), Status otherwise, nothing decided before the first checks; a
configured screen that is not live is a warning, not Setup; Done waits for the essential checks;
Skip clears the server; Settings shows every error.

Run from desktop/:  python3 ui/tests/ui_test.py [SCREENSHOT_DIR]
(with the Python `playwright` package and its Chromium installed; it starts its own Vite
server). With SCREENSHOT_DIR, it also saves screenshots of every Setup step, Status state,
Settings and How capture works.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

UI = Path(__file__).resolve().parent.parent
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_vite(port: int) -> subprocess.Popen:
    if shutil.which("node") is None:
        raise SystemExit("node is not on PATH (Vite needs it)")
    vite = UI / "node_modules" / ".bin" / "vite"
    proc = subprocess.Popen([str(vite), "--host", "127.0.0.1", "--port", str(port), "--strictPort"], cwd=UI, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise SystemExit("vite did not start")


class Ui:
    def __init__(self, browser, base: str):
        self.browser, self.base = browser, base

    def open(self, query: str, scheme: str = "light", wait: str = ".setup, .app") -> Page:
        page = self.browser.new_page(viewport={"width": 980, "height": 760}, color_scheme=scheme)
        page.on("pageerror", lambda e: (_ for _ in ()).throw(AssertionError(f"page error: {e}")))
        page.goto(f"{self.base}/mock.html?{query}")
        if wait:
            page.wait_for_selector(wait)
        page.wait_for_timeout(500)
        return page


def shown(page: Page) -> str:
    if page.locator(".setup").count():
        return "setup"
    if page.locator(".now").count():
        return "status"
    if page.locator(".app").count():
        return "pages"
    return "nothing"


def check(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError(what)
    print(f"ok  {what}")


def tests(ui: Ui) -> None:
    for scenario, want in [("empty", "setup"), ("setup", "setup"), ("health-fail", "setup"), ("two", "status"), ("screen-not-live", "status")]:
        page = ui.open(f"scenario={scenario}")
        check(shown(page) == want, f"{scenario}: {want} is shown (got {shown(page)})")
        page.close()

    page = ui.open("scenario=loading", wait="")
    page.wait_for_timeout(1200)
    check(shown(page) == "nothing", "before the first checks nothing is decided (no Setup flash, no empty Status)")
    page.close()

    page = ui.open("scenario=health-fail")
    check(page.locator(".setup-steps button.on").inner_text().endswith("Input access"), "a later essential failure opens Setup at its step, not Welcome")
    page.close()

    page = ui.open("scenario=screen-not-live&page=status")
    check(page.locator(".check-title", has_text="Screens").count() == 1, "a screen that is not live is a warning on Status")
    page.close()

    page = ui.open("scenario=setup&step=5")
    check(page.locator(".setup-nav button.button-primary").is_disabled(), "Done waits while an essential check fails")
    page.click("button:has-text('Check again')")
    page.wait_for_timeout(300)
    check(page.locator(".setup-nav button.button-primary").is_disabled(), "…and after checking again")
    page.close()

    page = ui.open("scenario=setup-done&setup=1&step=5")
    check(page.locator(".capture-paused").count() == 1, "the first start is paused")
    start = page.locator(".setup-nav button.button-primary")
    check(start.inner_text() == "Start recording" and start.is_enabled(), "Start recording is ready when the essential checks pass")
    start.click()
    page.wait_for_selector(".now")
    check(shown(page) == "status", "Start recording leaves Setup for Status")
    check(page.locator(".capture-paused").count() == 0, "…and recording has started")
    page.close()

    page = ui.open("scenario=setup-done&setup=1&step=5")
    page.click(".setup-nav button:has-text('Done, stay paused')")
    page.wait_for_selector(".now")
    check(shown(page) == "status" and page.locator(".capture-paused").count() == 1, "Done, stay paused leaves Setup still paused")
    page.close()

    page = ui.open("scenario=setup&step=2")
    page.click("text=Skip this step")
    page.click("button:has-text('Keep them here')")
    page.wait_for_timeout(300)
    check(page.evaluate("window.savedSettings && window.savedSettings.server") == "", "Skip clears the server")
    check(page.locator(".setup-steps button.on").inner_text().endswith("Screens"), "…and goes on to Screens")
    page.close()

    page = ui.open("scenario=two&page=settings")
    page.click(".advanced summary")
    page.fill("#end_after_idle_s", "")
    page.fill("#change_levels", "300")
    page.click("button:has-text('Save')")
    page.wait_for_timeout(300)
    check(page.locator("details.advanced").get_attribute("open") is not None, "an error in Advanced opens it")
    check(page.locator(".field-error").count() == 2, "every field error is shown")
    check(page.locator(".note-error", has_text="Not saved").count() == 1, "…with a note that nothing was saved")
    page.close()

    page = ui.open("scenario=two&page=settings")
    check(page.locator("details.advanced").get_attribute("open") is None, "Capture timing is folded under Advanced by default")
    import re
    check(re.search(r"\bR[0-9]\b", page.locator("main").inner_text()) is None, "no rule numbers on Settings")
    page.click(".advanced summary")
    page.click("text=How these are used")
    page.wait_for_selector(".rules")
    check(page.locator(".rule").count() == 6, "How these are used leads to the six rules")
    check(re.search(r"\bR[0-9]\b", page.locator("main").inner_text()) is None, "no rule codes on How capture works")
    page.close()

    page = ui.open("scenario=two&page=settings&problems=1")
    check(page.locator(".banner", has_text="change_levels = 300").count() == 1, "invalid values in client.toml are shown on Settings")
    page.close()


def screenshots(ui: Ui, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for step in range(6):
        ui.open(f"scenario=setup&step={step}").screenshot(path=out / f"setup-{step + 1}.png", full_page=True)
    ui.open("scenario=setup-done&setup=1&step=5").screenshot(path=out / "setup-6-all-set.png", full_page=True)
    page = ui.open("scenario=setup&step=2")
    page.click("button:has-text('Test')")
    page.wait_for_timeout(300)
    page.screenshot(path=out / "setup-3-tested.png", full_page=True)
    page.click("text=Skip this step")
    page.screenshot(path=out / "setup-3-skip.png", full_page=True)
    for name, scenario in [("active", "two"), ("idle", "idle"), ("paused", "paused"), ("offline", "offline"),
                           ("rejected", "rejected"), ("attention", "screen-not-live")]:
        ui.open(f"scenario={scenario}&page=status").screenshot(path=out / f"status-{name}.png")
    ui.open("scenario=paused&page=status", "dark").screenshot(path=out / "status-paused-dark.png")
    ui.open("scenario=two&page=settings").screenshot(path=out / "settings.png", full_page=True)
    ui.open("scenario=two&page=settings", "dark").screenshot(path=out / "settings-dark.png", full_page=True)
    page = ui.open("scenario=two&page=settings")
    page.click(".advanced summary")
    page.wait_for_timeout(200)
    page.locator(".advanced").scroll_into_view_if_needed()
    page.screenshot(path=out / "settings-advanced.png")
    page.fill("#change_levels", "300")
    page.click("button:has-text('Save')")
    page.wait_for_timeout(300)
    page.locator(".field-error").first.scroll_into_view_if_needed()
    page.screenshot(path=out / "settings-error.png")
    ui.open("scenario=two&page=settings&problems=1").screenshot(path=out / "settings-problems.png", full_page=True)
    ui.open("scenario=two&page=how").screenshot(path=out / "how-it-works.png", full_page=True)
    for scenario in ["two", "duplicate", "needs-pick", "stopped"]:
        ui.open(f"scenario={scenario}&page=screens").screenshot(path=out / f"screens-{scenario}.png")


def main() -> None:
    port = int(os.environ.get("UI_TEST_PORT") or free_port())
    vite = start_vite(port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ui = Ui(browser, f"http://127.0.0.1:{port}")
            tests(ui)
            if SHOTS:
                screenshots(ui, SHOTS)
            browser.close()
    finally:
        vite.terminate()
    print("all passed")


if __name__ == "__main__":
    main()
