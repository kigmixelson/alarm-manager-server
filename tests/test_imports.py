"""Check cold imports: pytest collection must not hide import-order failures."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("module", [
    "alarm_manager_server.worker.run",
    "alarm_manager_server.worker.ticket_handlers",
    "alarm_manager_server.plugins.registry",
    "alarm_manager_server.plugins.oracle",
    "alarm_manager_server.plugins.bitrix24",
])
def test_cold_import(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import importlib; importlib.import_module({module!r})"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_package_exports():
    from alarm_manager_server.worker import main
    from alarm_manager_server.worker.run import main as entrypoint
    from alarm_manager_server.plugins import discover_ticket_handlers
    from alarm_manager_server.plugins.registry import discover_ticket_handlers as discover

    assert main is entrypoint
    assert discover_ticket_handlers is discover
