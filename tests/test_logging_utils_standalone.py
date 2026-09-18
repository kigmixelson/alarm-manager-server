"""Run with python3 tests/test_logging_utils_standalone.py; no extra dependencies."""
import importlib.util
import logging
from pathlib import Path
import unittest
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("logging_utils", Path(__file__).resolve().parents[1] / "alarm_manager_server/logging_utils.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LoggingTests(unittest.TestCase):
    def test_oracle_error(self):
        exc = Exception(SimpleNamespace(full_code="ORA-01017"))
        self.assertEqual(module.error_summary(exc), "Oracle: авторизация отклонена (ORA-01017)")

    def test_html_response_hidden(self):
        exc = Exception("POST /users/session failed with status 502: <html>secret\n</html>")
        summary = module.error_summary(exc)
        self.assertIn("HTTP 502", summary)
        self.assertNotIn("secret", summary)
        self.assertNotIn("\n", summary)

    def test_unknown_error_hidden(self):
        self.assertNotIn("password", module.error_summary(ValueError("password=secret")))

    def test_traceback_debug_only(self):
        logger = logging.getLogger("compact-test")
        for level in [logging.INFO, logging.DEBUG]:
            with self.subTest(level=level), self.assertLogs(logger, level=level) as captured:
                try:
                    raise TimeoutError("raw detail")
                except TimeoutError:
                    module.log_error(logger, "failed ticket=%s", "T-1")
            self.assertIn("ticket=T-1", captured.output[0])
            self.assertIsNone(captured.records[0].exc_info)
            if level == logging.INFO:
                self.assertEqual(len(captured.records), 1)
                self.assertNotIn("Traceback", captured.output[0])
            else:
                self.assertIsNotNone(captured.records[1].exc_info)


if __name__ == "__main__":
    unittest.main()
