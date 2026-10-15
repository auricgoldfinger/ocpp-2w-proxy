"""The monitor's browser UI: its pure ES modules pass their node tests."""

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parent.parent / "ocpp_2w_proxy" / "monitor" / "static"


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
