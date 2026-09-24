import os
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch

from skew_monitor import (
    Market,
    decimal_value,
    find_risk_limit,
    edit_telegram_message,
    format_live_status_message,
    format_oi_limit_message,
    is_oi_limit_reached,
    LiveMessageUnavailableError,
    load_dotenv,
)


class RiskLimitParsingTests(unittest.TestCase):
    def test_parses_direct_record(self):
        current, limit = find_risk_limit({"current_skew_usd": "12.50", "skew_limit_usd": 20})
        self.assertEqual((current, limit), (Decimal("12.50"), Decimal("20")))

    def test_parses_wrapped_list(self):
        current, limit = find_risk_limit({"data": [{"ignored": True}, {"current_skew_usd": 1, "skew_limit_usd": 2}]})
        self.assertEqual((current, limit), (Decimal("1"), Decimal("2")))

    def test_rejects_missing_fields(self):
        with self.assertRaises(LookupError):
            find_risk_limit({"data": []})

    def test_rejects_boolean_number(self):
        with self.assertRaises(ValueError):
            decimal_value(True, "current_skew_usd")

    def test_true_oi_limit_message(self):
        message = format_oi_limit_message(Market("ONE", "perpetual_future"), Decimal("2"), Decimal("1"), True)
        self.assertIn("🔴 <b>OI limit reached</b>", message)
        self.assertIn("<code>$2.00</code>", message)

    def test_false_oi_limit_message(self):
        message = format_oi_limit_message(Market("ONE", "perpetual_future"), Decimal("20764.356504"), Decimal("73163.0956"), False)
        self.assertIn("🟢 <b>OI limit not reached</b>", message)
        self.assertIn("<code>$20,764.36</code>", message)
        self.assertIn("<code>$73,163.10</code>", message)

    def test_oi_limit_is_reached_only_above_limit(self):
        self.assertTrue(is_oi_limit_reached(Decimal("73163.0956"), Decimal("20764.356504")))
        self.assertFalse(is_oi_limit_reached(Decimal("20764.356504"), Decimal("73163.0956")))
        self.assertFalse(is_oi_limit_reached(Decimal("20"), Decimal("20")))

    def test_live_status_includes_timestamp_and_current_values(self):
        message = format_live_status_message(
            Market("ONE", "perpetual_future"),
            Decimal("73163.0956"),
            Decimal("20764.356504"),
            False,
            datetime(2026, 9, 23, 12, 34, 56, tzinfo=timezone.utc),
        )
        self.assertIn("Live status", message)
        self.assertIn("Updated <code>2026-09-23 12:34:56 UTC</code>", message)
        self.assertIn("<code>$73,163.10</code>", message)

    def test_only_explicit_missing_message_error_discards_live_card(self):
        error = HTTPError(
            "https://api.telegram.org/botTOKEN/editMessageText",
            400,
            "Bad Request",
            None,
            BytesIO(b'{"ok":false,"description":"Bad Request: message to edit not found"}'),
        )
        with patch("skew_monitor.telegram_request", side_effect=error):
            with self.assertRaises(LiveMessageUnavailableError):
                edit_telegram_message("token", "chat", 123, "text", 10)

    def test_network_failure_keeps_live_card(self):
        with patch("skew_monitor.telegram_request", side_effect=ConnectionResetError("reset")):
            with self.assertRaises(ConnectionResetError):
                edit_telegram_message("token", "chat", 123, "text", 10)

    def test_load_dotenv_does_not_override_shell_value(self):
        old_value = os.environ.get("SKEW_MONITOR_TEST_VALUE")
        os.environ["SKEW_MONITOR_TEST_VALUE"] = "from-shell"
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as dotenv:
                dotenv.write("SKEW_MONITOR_TEST_VALUE=from-file\n")
                path = dotenv.name
            try:
                load_dotenv(path)
                self.assertEqual(os.environ["SKEW_MONITOR_TEST_VALUE"], "from-shell")
            finally:
                os.unlink(path)
        finally:
            if old_value is None:
                os.environ.pop("SKEW_MONITOR_TEST_VALUE", None)
            else:
                os.environ["SKEW_MONITOR_TEST_VALUE"] = old_value


if __name__ == "__main__":
    unittest.main()
