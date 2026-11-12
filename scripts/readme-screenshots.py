"""Recreate the README's four screenshots (#713) from a clean start, on the scripted fake model.

    uv run --project frontend/e2e python scripts/readme-screenshots.py
    uv run --project frontend/e2e python scripts/readme-screenshots.py --out /some/folder
    uv run --project frontend/e2e python scripts/readme-screenshots.py --skip-build

It builds the front end (unless --skip-build), starts its own Vouch on a spare port (the built
front end served by the API on one origin, as frontend/e2e/run.sh does), in local sign-in mode on
the bundled fake model and in-memory storage: no key, no database URL and no Langfuse keys reach
it, so nothing is paid for and no live data is read or written. Then, in Chromium at 1280px wide
in light mode, it:

1. runs Reset demo on Nordlicht from the Developer page;
2. captures the Overview (overview.png): "12 of 12 controls accepted", "Compared with Q2 2026";
3. opens Company › Rounds › Q2 2026 (round-comparison.png): Better / Worse;
4. opens the Report (report.png): the readiness report's top section;
5. opens 5.1.1, selects Assess again, and captures its working paper (working-paper.png): the
   two-section draft answer, the quotes with "Quote verified", and the stale flag. Last, so the
   other three show Nordlicht's demo state as Reset demo leaves it.

Each image is clipped to its content and must be under 400 KB. A phrase the brief names that is
not on screen stops the run, rather than an image that lacks it. The server is stopped at the end,
whether the run passed or not.

Needs: backend deps (uv sync), front-end deps (npm ci in frontend/), and the browser:
    uv run --project frontend/e2e playwright install chromium
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Locator, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
WIDTH, HEIGHT = 1280, 900
MAX_BYTES = 400 * 1024
OWNER_PORTS = {5432, 8000, 5173}
"""Ports the owner's own database and servers use: never bound here."""
NORDLICHT = "Nordlicht Komponenten GmbH"
KEPT_ENV = ("PATH", "HOME")
"""The only variables the server inherits: no key, database URL or Langfuse key gets through."""


def spare_port() -> int:
    """A free port the OS picks, never one of the owner's."""
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in OWNER_PORTS:
            return port


def build_front_end() -> None:
    print("Building the front end…", flush=True)
    subprocess.run(
        ["npm", "run", "build"], cwd=ROOT / "frontend", check=True, stdout=subprocess.DEVNULL
    )


@contextmanager
def vouch_server(log: Path) -> Iterator[str]:
    """A throwaway Vouch on a spare port, stopped (with its process group) on the way out."""
    port = spare_port()
    env = {name: os.environ[name] for name in KEPT_ENV if name in os.environ}
    env |= {
        "VOUCH_AUTH_MODE": "local",
        "VOUCH_FRONTEND": str(ROOT / "frontend" / "dist"),
        "VOUCH_RATE_REQUESTS": "100000",
    }
    with log.open("w") as out:
        server = subprocess.Popen(
            ["uv", "run", "uvicorn", "vouch.api:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"{url}/api/health", timeout=2).close()
                break
            except OSError:
                if server.poll() is not None or time.monotonic() > deadline:
                    sys.exit(f"Vouch didn't start; its log:\n{log.read_text()[-3000:]}")
                time.sleep(0.5)
        print(f"Vouch is running on {url} (log: {log})", flush=True)
        yield url
    finally:
        try:
            os.killpg(server.pid, signal.SIGTERM)
            server.wait(timeout=15)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            os.killpg(server.pid, signal.SIGKILL)
        print("Vouch stopped.", flush=True)


def shoot(page: Page, name: str, top: Locator, bottom: Locator, out: Path) -> Path:
    """The page from `top`'s top edge to `bottom`'s bottom edge, the full width, as out/name.

    The viewport is made as tall as the whole page first, so the page's sticky and fixed parts
    (the review bar, the Ask Vouch button) sit at the page's foot, not over the content."""
    page.evaluate("window.scrollTo(0, 0)")
    height = page.evaluate("document.documentElement.scrollHeight")
    page.set_viewport_size({"width": WIDTH, "height": max(HEIGHT, int(height))})
    page.evaluate("window.scrollTo(0, 0)")
    first, last = top.bounding_box(), bottom.bounding_box()
    assert first and last, f"{name}: nothing to clip to"
    y0 = max(0, first["y"] - 16)
    clip = {"x": 0, "y": y0, "width": WIDTH, "height": last["y"] + last["height"] + 24 - y0}
    path = out / name
    page.screenshot(path=path, clip=clip, animations="disabled")
    page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
    size = path.stat().st_size
    print(f"  {path}  {size // 1024} KB", flush=True)
    if size >= MAX_BYTES:
        sys.exit(f"{name} is {size // 1024} KB, over the 400 KB limit.")
    return path


def reset_nordlicht(page: Page, url: str) -> None:
    with page.expect_response("**/api/developer/settings"):
        page.goto(f"{url}/developer")
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response("**/api/developer/profiles/nordlicht/reset-demo", timeout=120_000):
        page.get_by_role("button", name=f"Reset the demo of {NORDLICHT}").click()
    expect(page.get_by_role("region", name="Companies").get_by_role("status")).to_have_text(
        f"Reset {NORDLICHT} to its demo state: Q2 2026 closed, Q3 2026 open.", timeout=60_000
    )


def overview(page: Page, out: Path) -> None:
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Overview").click()
    main = page.locator("main")
    expect(main).to_contain_text("12 of 12 controls accepted", timeout=30_000)
    compared = page.locator("#compare-title")
    expect(compared).to_have_text("Compared with Q2 2026")
    section = page.locator("section", has=compared).last
    shoot(page, "overview.png", page.locator("header").first, section, out)


def working_paper(page: Page, url: str, out: Path) -> None:
    page.goto(f"{url}/controls/5.1.1")
    page.get_by_role("button", name="Assess again").click()
    main = page.locator("main")
    expect(main.get_by_text("Quote verified").first).to_be_visible(timeout=120_000)
    expect(main).to_contain_text(re.compile(r"Stale: approved"), timeout=30_000)
    expect(page.get_by_role("button", name="Assess again")).to_be_enabled(timeout=120_000)
    shoot(
        page,
        "working-paper.png",
        page.locator("header").first,
        main.get_by_text("Quote verified").first,
        out,
    )


def round_comparison(page: Page, out: Path) -> None:
    with page.expect_response(re.compile(r".*/api/rounds$")):
        page.get_by_role("navigation", name="Main").get_by_role("link", name="Company").click()
    rounds = page.get_by_role("region", name="Rounds", exact=True)
    with page.expect_response(re.compile(r".*/api/rounds/\d+/comparison$")):
        rounds.get_by_role("listitem").filter(has_text="Q2 2026").get_by_role("link").click()
    changes = page.get_by_role("region", name="Summary").get_by_role("list", name="Changes")
    expect(changes).to_contain_text("Better")
    expect(changes).to_contain_text("Worse")
    shoot(page, "round-comparison.png", page.locator("header").first, page.locator("main"), out)


def report(page: Page, out: Path) -> None:
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Report").click()
    readiness = page.locator("section[aria-labelledby=report-readiness]")
    expect(readiness).to_contain_text("12 of 12 controls accepted", timeout=30_000)
    shoot(page, "report.png", page.locator("header").first, readiness, out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=OUT, help="where the PNGs go (docs/screenshots)"
    )
    parser.add_argument("--skip-build", action="store_true", help="use frontend/dist as it is")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.skip_build:
        build_front_end()
    with (
        tempfile.TemporaryDirectory() as tmp,
        vouch_server(Path(tmp) / "vouch.log") as url,
        sync_playwright() as p,
    ):
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": WIDTH, "height": HEIGHT},
            color_scheme="light",
            device_scale_factor=1,
        )
        reset_nordlicht(page, url)
        print("Reset demo on Nordlicht done. Capturing:", flush=True)
        overview(page, args.out)
        round_comparison(page, args.out)
        report(page, args.out)
        working_paper(page, url, args.out)  # last: the others show the demo state as reset
        browser.close()


if __name__ == "__main__":
    main()
