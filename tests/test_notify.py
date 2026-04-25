"""Tests for notification module."""

from unittest.mock import MagicMock, patch
from urllib.error import URLError
import pytest
from moomoo_bot.notify import (
    send_webhook,
    notify_rebalance,
    notify_risk_stop,
    notify_error,
    notify_daily_summary,
    notify_kill_switch,
    notify_daily_limit,
)


class TestSendWebhook:
    def test_send_webhook_success(self):
        mock_response = MagicMock()
        mock_response.status = 200
        with patch("moomoo_bot.notify.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = mock_response
            result = send_webhook("http://example.com/webhook", {"event": "test"})
            assert result is True
            mock_urlopen.assert_called_once()
            request = mock_urlopen.call_args[0][0]
            assert request.method == "POST"
            assert request.get_header("Content-type") == "application/json"

    def test_send_webhook_empty_url(self):
        assert send_webhook("", {"event": "test"}) is False
        assert send_webhook(None, {"event": "test"}) is False

    def test_send_webhook_failure_returns_false(self):
        with patch("moomoo_bot.notify.urlopen", side_effect=URLError("timeout")):
            result = send_webhook("http://example.com/webhook", {"event": "test"})
            assert result is False

    def test_send_webhook_url_error_returns_false(self):
        with patch(
            "moomoo_bot.notify.urlopen", side_effect=OSError("connection refused")
        ):
            result = send_webhook("http://example.com/webhook", {"event": "test"})
            assert result is False

    def test_send_webhook_timeout_returns_false(self):
        with patch("moomoo_bot.notify.urlopen", side_effect=TimeoutError("timeout")):
            result = send_webhook("http://example.com/webhook", {"event": "test"})
            assert result is False

    def test_send_webhook_non_200_status(self):
        mock_response = MagicMock()
        mock_response.status = 404
        with patch("moomoo_bot.notify.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value = mock_response
            result = send_webhook("http://example.com/webhook", {"event": "test"})
            assert result is False


class TestNotifyRebalance:
    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_rebalance(self, mock_send):
        notify_rebalance("http://hook", "AAPL", "BUY", 100, 150.0, "momentum", "paper")
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        assert payload["event"] == "rebalance"
        assert payload["mode"] == "paper"
        assert payload["symbol"] == "AAPL"
        assert payload["side"] == "BUY"
        assert payload["quantity"] == 100
        assert payload["price"] == 150.0
        assert payload["estimated_value"] == 15000.0
        assert payload["reason"] == "momentum"

    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_rebalance_default_mode(self, mock_send):
        notify_rebalance("http://hook", "TSLA", "SELL", 50, 200.0, "stop_loss")
        payload = mock_send.call_args[0][1]
        assert payload["mode"] == "paper"


class TestNotifyRiskStop:
    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_risk_stop(self, mock_send):
        notify_risk_stop("http://hook", "max_drawdown", 85000.0, 100000.0, 0.15)
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        assert payload["event"] == "risk_stop"
        assert payload["reason"] == "max_drawdown"
        assert payload["account_value"] == 85000.0
        assert payload["peak_value"] == 100000.0
        assert payload["drawdown_pct"] == 0.15


class TestNotifyError:
    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_error(self, mock_send):
        notify_error("http://hook", "connection lost", 3)
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        assert payload["event"] == "error"
        assert payload["message"] == "connection lost"
        assert payload["consecutive_failures"] == 3

    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_error_no_failures(self, mock_send):
        notify_error("http://hook", "some error", 0)
        payload = mock_send.call_args[0][1]
        assert payload["consecutive_failures"] == 0


class TestNotifyDailySummary:
    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_daily_summary(self, mock_send):
        notify_daily_summary(
            "http://hook",
            105000.0,
            0.02,
            0.10,
            0.05,
            {"AAPL": 100, "GOOGL": 50},
            False,
        )
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        assert payload["event"] == "daily_summary"
        assert payload["account_value"] == 105000.0
        assert payload["day_return_pct"] == 0.02
        assert payload["total_return_pct"] == 0.10
        assert payload["drawdown_pct"] == 0.05
        assert payload["positions"] == {"AAPL": 100, "GOOGL": 50}
        assert payload["halted"] is False

    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_daily_summary_halted(self, mock_send):
        notify_daily_summary(
            "http://hook",
            95000.0,
            -0.03,
            -0.05,
            0.05,
            {},
            True,
        )
        payload = mock_send.call_args[0][1]
        assert payload["halted"] is True


class TestNotifyKillSwitch:
    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_kill_switch(self, mock_send):
        notify_kill_switch("http://hook")
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        assert payload["event"] == "kill_switch"
        assert "Kill switch file detected" in payload["message"]


class TestNotifyDailyLimit:
    @patch("moomoo_bot.notify.send_webhook")
    def test_notify_daily_limit(self, mock_send):
        notify_daily_limit("http://hook", 0.05, 95000.0)
        mock_send.assert_called_once()
        payload = mock_send.call_args[0][1]
        assert payload["event"] == "daily_loss_limit"
        assert payload["loss_pct"] == 0.05
        assert payload["account_value"] == 95000.0
        assert "Daily loss limit breached" in payload["message"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
