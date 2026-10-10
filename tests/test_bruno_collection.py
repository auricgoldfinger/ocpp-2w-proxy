"""The Bruno collection has one valid request per charger-bound OCPP action."""

import json
import re
from pathlib import Path

import pytest

from ocpp_2w_proxy import CHARGER_BOUND_ACTIONS

COMMANDS_DIR = Path(__file__).resolve().parent.parent / "bruno" / "commands"
BODY_BLOCK = re.compile(r"^body:json \{\n(.*?)\n\}$", re.MULTILINE | re.DOTALL)


def _body(path: Path) -> dict:
    match = BODY_BLOCK.search(path.read_text())
    assert match, f"{path.name} has no body:json block"
    return json.loads(match.group(1).replace("{{timeout}}", "30"))


def _action_files() -> dict[str, list[Path]]:
    files: dict[str, list[Path]] = {}
    for path in sorted(COMMANDS_DIR.glob("*.bru")):
        files.setdefault(path.stem.split("-")[0], []).append(path)
    return files


@pytest.mark.parametrize("action", sorted(CHARGER_BOUND_ACTIONS))
def test_every_charger_bound_action_has_a_request(action: str) -> None:
    assert action in _action_files()


def test_no_request_for_unknown_actions() -> None:
    assert set(_action_files()) <= set(CHARGER_BOUND_ACTIONS)


@pytest.mark.parametrize(
    "path",
    sorted(COMMANDS_DIR.glob("*.bru")),
    ids=lambda p: p.stem,
)
def test_body_is_valid_json_matching_filename(path: Path) -> None:
    body = _body(path)
    assert body["action"] == path.stem.split("-")[0]
    assert isinstance(body["payload"], dict)
    assert body["timeout"] == 30
