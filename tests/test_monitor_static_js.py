"""The monitor's browser UI: its pure ES modules pass their node tests, every asset it references
exists, and nothing in it needs more than CSP ``default-src 'self'``."""

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

from ocpp_2w_proxy.reasons import Reason

STATIC_DIR = Path(__file__).resolve().parent.parent / "ocpp_2w_proxy" / "monitor" / "static"
APP_MODULES = sorted(path for path in STATIC_DIR.rglob("*.js") if "tests" not in path.relative_to(STATIC_DIR).parts)
# Modules that branch on reason codes.
REASON_MODULES = ["components/badge.js"]
ALL_MODULES = sorted(STATIC_DIR.rglob("*.js"))
IMPORT = re.compile(
    r"""(?:^|;|\n)\s*(?:import|export)\s(?:[^;'"]*?\sfrom\s*)?["']([^"']+)["']"""
    r"""|\bimport\s*\(\s*["']([^"']+)["']""",
)


class _AssetCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []
        self.inline_scripts = 0
        self.inline_styles = 0
        self.handlers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script":
            if values.get("src"):
                self.assets.append(values["src"])
            else:
                self.inline_scripts += 1
        if tag == "link" and values.get("href"):
            self.assets.append(values["href"])
        if tag == "style":
            self.inline_styles += 1
        self.inline_styles += "style" in values
        self.handlers += [name for name in values if name.startswith("on")]


def _index() -> _AssetCollector:
    collector = _AssetCollector()
    collector.feed((STATIC_DIR / "index.html").read_text())
    return collector


def _imports(module: Path) -> list[str]:
    return [first or second for first, second in IMPORT.findall(module.read_text())]


def _resolves_inside_static(base: Path, reference: str) -> bool:
    target = (base / reference).resolve()
    return target.is_relative_to(STATIC_DIR) and target.is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_static_js_node_tests_pass() -> None:
    result = subprocess.run(
        ["node", "--test", "tests/**/*.test.js"],
        cwd=STATIC_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_asset_in_index_html_exists() -> None:
    assets = _index().assets
    assert "app.js" in assets and "styles.css" in assets
    for asset in assets:
        assert not re.match(r"^[a-z]+:|^//", asset), f"{asset} is not a local asset"
        assert _resolves_inside_static(STATIC_DIR, asset), asset


@pytest.mark.parametrize("module", ALL_MODULES, ids=lambda path: str(path.relative_to(STATIC_DIR)))
def test_every_module_import_resolves(module: Path) -> None:
    for specifier in _imports(module):
        if specifier.startswith("node:") and "tests" in module.relative_to(STATIC_DIR).parts:
            continue
        assert specifier.startswith(("./", "../")), f"{specifier} is not a relative import"
        assert _resolves_inside_static(module.parent, specifier), f"{specifier} from {module.name}"


def test_import_scanner_sees_imports() -> None:
    assert "./views/exchanges.js" in _imports(STATIC_DIR / "app.js")


def test_reason_codes_in_the_ui_and_fixtures_exist() -> None:
    sources = [(STATIC_DIR / module).read_text() for module in REASON_MODULES]
    named = {code for source in sources for code in re.findall(r"""["']([A-Z][A-Z_]{3,})["']""", source)}
    fixtures = [path.read_text() for path in (STATIC_DIR / "tests" / "fixtures").glob("*.json")]
    named |= {code for text in fixtures for code in re.findall(r'"reason": "([A-Z_]+)"', text)}
    assert named, "the scan found no reason codes"
    assert named <= set(Reason.__members__), named - set(Reason.__members__)


def test_index_html_needs_no_inline_script_or_style() -> None:
    index = _index()
    assert index.inline_scripts == 0
    assert index.inline_styles == 0
    assert index.handlers == []


@pytest.mark.parametrize("module", APP_MODULES, ids=lambda path: str(path.relative_to(STATIC_DIR)))
def test_modules_stay_within_the_csp(module: Path) -> None:
    source = module.read_text()
    assert not re.search(r"""\sstyle=["']|\son[a-z]+=["']""", source), "inline style or handler in markup"
    assert "eval(" not in source and "new Function" not in source
    if module.name != "html.js":
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            assert sink not in source, f"{sink}: assign markup through html.setHtml"
