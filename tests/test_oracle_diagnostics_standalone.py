"""Run without project dependencies: python3 tests/test_oracle_diagnostics_standalone.py."""
import importlib.util
from pathlib import Path
import socket
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location(
    "oracle_diagnostics", Path(__file__).resolve().parents[1] /
    "alarm_manager_server/plugins/oracle_diagnostics.py",
)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class DiagnosticsTests(unittest.TestCase):
    def test_dns_failure(self):
        with patch.object(diag.socket, "getaddrinfo", side_effect=socket.gaierror()), self.assertLogs(diag.logger) as logs:
            diag.probe_endpoints("T-1", [("invalid.test", 1523)])
        self.assertIn("DNS FAILED", "\n".join(logs.output))

    def test_tcp_success_and_refusal(self):
        for error, expected in [(None, "TCP OK"), (ConnectionRefusedError(111, "refused"), "TCP FAILED")]:
            with self.subTest(expected=expected):
                sock = MagicMock()
                sock.__enter__.return_value.connect.side_effect = error
                with patch.object(diag.socket, "getaddrinfo", return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 1523))
                ]), patch.object(diag.socket, "socket", return_value=sock), self.assertLogs(diag.logger) as logs:
                    diag.probe_endpoints("T-1", [("db.test", 1523)])
                self.assertIn(expected, "\n".join(logs.output))

    def test_auth_classification_and_timeout(self):
        driver = MagicMock()
        driver.is_thin_mode.return_value = False
        driver.ConnectParams.return_value.host = "db.test"
        driver.ConnectParams.return_value.port = 1523
        cfg = SimpleNamespace(oracle_mode="thick", oracle_dsn="//db.test:1523/db")
        error = Exception(SimpleNamespace(full_code="ORA-01017"))
        with patch.object(diag.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 15)), self.assertLogs(diag.logger) as logs:
            diag.diagnose_connection(driver, cfg, error, "T-1")
        output = "\n".join(logs.output)
        self.assertIn("авторизация отклонена", output)
        self.assertIn("15 секунд", output)
        driver.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
