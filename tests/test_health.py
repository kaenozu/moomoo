"""Health check server tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import time


from moomoo_bot.health import HealthStatus, HealthCheckServer


class TestHealthStatus:
    """Tests for HealthStatus dataclass."""

    def test_health_status_initialization(self) -> None:
        status = HealthStatus(
            is_healthy=True,
            timestamp="2026-05-01T10:00:00Z",
            account_value=100000.0,
            risk_halted=False,
            trade_count=5,
        )

        assert status.is_healthy is True
        assert status.account_value == 100000.0
        assert status.risk_halted is False
        assert status.trade_count == 5

    def test_health_status_with_error(self) -> None:
        status = HealthStatus(
            is_healthy=False,
            timestamp="2026-05-01T10:00:00Z",
            last_error="Connection timeout",
        )

        assert status.is_healthy is False
        assert status.last_error == "Connection timeout"

    def test_health_status_defaults(self) -> None:
        status = HealthStatus(
            is_healthy=True,
            timestamp="2026-05-01T10:00:00Z",
        )

        assert status.account_value is None
        assert status.risk_halted is False
        assert status.trade_count == 0
        assert status.uptime_seconds == 0.0
        assert status.equity_curve_points == 0


class TestHealthCheckServer:
    """Tests for HealthCheckServer."""

    def test_server_initialization(self) -> None:
        server = HealthCheckServer(port=18080)

        assert server.port == 18080
        assert server.server is None
        assert server.thread is None

    def test_server_initialization_default_port(self) -> None:
        server = HealthCheckServer()

        assert server.port == 8080

    def test_server_update_status(self) -> None:
        server = HealthCheckServer(port=18082)

        # Update status with custom values
        server.update_status(
            is_healthy=True,
            account_value=50000.0,
            risk_halted=True,
            trade_count=10,
        )

        # Get status and verify update
        status = server._get_status()
        assert status.is_healthy is True
        assert status.account_value == 50000.0
        assert status.risk_halted is True
        assert status.trade_count == 10

    def test_server_start_and_stop(self) -> None:
        server = HealthCheckServer(port=18081)

        try:
            server.start()
            assert server.server is not None
            assert server.thread is not None
            assert server.thread.is_alive()

            time.sleep(0.2)  # Give server time to start

            thread = server.thread  # Save thread reference before stop
            server.stop()
            # Give thread time to shut down
            thread.join(timeout=2)
            assert not thread.is_alive()
        finally:
            # Cleanup
            if server.server is not None:
                server.stop()

    def test_server_update_status_preserves_timestamp(self) -> None:
        """Test that status updates preserve proper timestamp."""
        server = HealthCheckServer(port=18083)

        before = datetime.now(timezone.utc)
        server.update_status(is_healthy=True)
        after = datetime.now(timezone.utc)

        status = server._get_status()
        status_time = datetime.fromisoformat(status.timestamp)

        # Timestamp should be between before and after
        assert before <= status_time <= after

    def test_server_uptime_increases(self) -> None:
        """Test that uptime increases over time."""
        server = HealthCheckServer(port=18084)

        status1 = server._get_status()
        uptime1 = status1.uptime_seconds

        time.sleep(0.1)

        status2 = server._get_status()
        uptime2 = status2.uptime_seconds

        assert uptime2 > uptime1

    def test_server_double_start_is_safe(self) -> None:
        """Test that calling start twice is safe."""
        server = HealthCheckServer(port=18085)

        try:
            server.start()
            server.start()  # Should warn but not crash

            assert server.server is not None
            assert server.thread is not None
        finally:
            server.stop()

    def test_server_stop_when_not_running(self) -> None:
        """Test that stopping when not running is safe."""
        server = HealthCheckServer(port=18086)

        # Should not crash
        server.stop()
        assert server.server is None


class TestHealthCheckIntegration:
    """Integration tests for health check system."""

    def test_health_check_reflects_trading_state(self) -> None:
        """Test that health check accurately reflects trading state."""
        server = HealthCheckServer(port=18085)

        # Initially healthy
        server.update_status(is_healthy=True)
        assert server._get_status().is_healthy is True

        # Simulate risk halt
        server.update_status(is_healthy=True, risk_halted=True)
        assert server._get_status().risk_halted is True

        # Simulate error
        server.update_status(is_healthy=False)
        assert server._get_status().is_healthy is False

    def test_health_status_serialization(self) -> None:
        """Test that health status can be serialized to JSON."""
        status = HealthStatus(
            is_healthy=True,
            timestamp="2026-05-01T10:00:00Z",
            account_value=100000.0,
            risk_halted=False,
            trade_count=5,
            equity_curve_points=252,
        )

        # Convert to dict (simulating JSON serialization)
        status_dict = {
            "is_healthy": status.is_healthy,
            "timestamp": status.timestamp,
            "account_value": status.account_value,
            "risk_halted": status.risk_halted,
            "trade_count": status.trade_count,
            "equity_curve_points": status.equity_curve_points,
        }

        # Should be JSON serializable
        json_str = json.dumps(status_dict)
        assert isinstance(json_str, str)
        assert "is_healthy" in json_str
