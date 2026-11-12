"""The GitHub Pages site (docs/, served from main) shares one nav bar across its pages (#704, build review added 2026-11-12).

Each page carries the same five links (Home, Process flow, Eval report, Build review, Repo), with its own one
marked `aria-current="page"`. The eval report's data is generated (scripts/eval-site.py writes
docs/evals/data.js); its page is hand-written, so a regeneration must leave the page, and the
nav in it, as it was."""

import importlib.util
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REPO = "https://github.com/DerykHopley/tc-vouch-compliance"

# page -> (its nav hrefs in order: Home, Process flow, Eval report, Build review, Repo; the current one's label)
PAGES = {
    "index.html": (["./", "flow/", "evals/", "build-review/", REPO], "Home"),
    "flow/index.html": (["../", "./", "../evals/", "../build-review/", REPO], "Process flow"),
    "evals/index.html": (["../", "../flow/", "./", "../build-review/", REPO], "Eval report"),
    "build-review/index.html": (["../", "../flow/", "../evals/", "./", REPO], "Build review"),
}
LABELS = ["Home", "Process flow", "Eval report", "Build review", "Repo"]


class NavLinks(HTMLParser):
    """Collect (href, label, aria-current) for each link inside <nav class="site-nav">."""

    def __init__(self) -> None:
        super().__init__()
        self.navs = 0
        self.depth = 0
        self.links: list[list[str | None]] = []
        self._in_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "nav" and "site-nav" in (a.get("class") or "").split():
            self.navs += 1
            self.depth = 1
        elif tag == "nav" and self.depth:
            self.depth += 1
        elif tag == "a" and self.depth:
            self.links.append([a.get("href"), "", a.get("aria-current")])
            self._in_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self.depth:
            self.depth -= 1
        elif tag == "a":
            self._in_link = False

    def handle_data(self, data: str) -> None:
        if self._in_link and self.links:
            self.links[-1][1] = str(self.links[-1][1]) + data


def nav_of(html: str) -> NavLinks:
    parser = NavLinks()
    parser.feed(html)
    return parser


@pytest.mark.parametrize("page", PAGES)
def test_each_page_has_the_shared_nav_with_its_own_link_marked(page: str) -> None:
    hrefs, current = PAGES[page]
    nav = nav_of((DOCS / page).read_text())

    assert nav.navs == 1, f"{page}: expected one site-nav, found {nav.navs}"
    assert [link[0] for link in nav.links] == hrefs
    assert [" ".join(str(link[1]).split()) for link in nav.links] == LABELS
    assert [link[1] for link in nav.links if link[2] == "page"] == [current]


def test_regenerating_the_eval_report_keeps_its_nav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location("eval_site", ROOT / "scripts" / "eval-site.py")
    assert spec is not None and spec.loader is not None
    site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(site)

    # The generator writes the data file only, beside the hand-written page.
    assert site.OUT == DOCS / "evals" / "data.js"

    evals = tmp_path / "evals"
    shutil.copytree(DOCS / "evals", evals)
    page_before = (evals / "index.html").read_text()
    (evals / "data.js").unlink()
    # The inputs stay the repo's kept results; only the output moves.
    monkeypatch.setattr(site, "ROOT", tmp_path)
    monkeypatch.setattr(site, "OUT", evals / "data.js")
    site.main()

    assert (evals / "data.js").read_text().startswith("// Built by scripts/eval-site.py")
    page_after = (evals / "index.html").read_text()
    assert page_after == page_before
    assert [link[1] for link in nav_of(page_after).links if link[2] == "page"] == ["Eval report"]
