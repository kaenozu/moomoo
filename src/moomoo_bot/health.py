"""Health check module.

Purpose: Simple HTTP health check endpoint for monitoring.
Related: orchestrator.py, broker modules.
"""

from __future__ import annotations

import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from threading import Lock, Thread
from dataclasses import dataclass
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    is_healthy: bool
    timestamp: str
    account_value: float | None = None
    risk_halted: bool = False
    last_error: str | None = None
    uptime_seconds: float = 0.0
    trade_count: int = 0
    equity_curve_points: int = 0


class HealthRequestHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, status_getter, **kwargs):
        self.status_getter = status_getter
        super().__init__(*args, **kwargs)

    def do_GET(self):
        try:
            status = self.status_getter()
            response = {
                "healthy": status.is_healthy,
                "timestamp": status.timestamp,
                "account_value": status.account_value,
                "risk_halted": status.risk_halted,
                "last_error": status.last_error,
                "uptime_seconds": status.uptime_seconds,
                "trade_count": status.trade_count,
                "equity_curve_points": status.equity_curve_points,
            }
            self.send_response(200 if status.is_healthy else 503)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
        except Exception as exc:
            logger.exception("Health check failed: %s", exc)
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


class HealthCheckServer:
    def __init__(self, port: int = 8080):
        self.port = port
        self.server: HTTPServer | None = None
        self.thread: Thread | None = None
        self._lock = Lock()
        self._status = HealthStatus(
            is_healthy=True, timestamp=datetime.now(timezone.utc).isoformat()
        )
        self._start_time = datetime.now(timezone.utc)

    def update_status(
        self,
        is_healthy: bool,
        account_value: float | None = None,
        risk_halted: bool = False,
        last_error: str | None = None,
        trade_count: int = 0,
        equity_curve_points: int = 0,
    ):
        with self._lock:
            self._status = HealthStatus(
                is_healthy=is_healthy,
                timestamp=datetime.now(timezone.utc).isoformat(),
                account_value=account_value,
                risk_halted=risk_halted,
                last_error=last_error,
                uptime_seconds=(
                    datetime.now(timezone.utc) - self._start_time
                ).total_seconds(),
                trade_count=trade_count,
                equity_curve_points=equity_curve_points,
            )

    def _get_status(self) -> HealthStatus:
        with self._lock:
            self._status.uptime_seconds = (
                datetime.now(timezone.utc) - self._start_time
            ).total_seconds()
            return self._status

    def start(self):
        """Start the health check server in a background thread."""
        if self.server is not None:
            logger.warning("Health check server already running")
            return

        def handler(*args, **kwargs):
            return HealthRequestHandler(*args, status_getter=self._get_status, **kwargs)

        self.server = HTTPServer(("127.0.0.1", self.port), handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info("Health check server started on port %d", self.port)

    def stop(self):
        """Stop the health check server."""
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            if self.thread is not None:
                self.thread.join(timeout=5.0)
            self.server = None
            self.thread = None
            logger.info("Health check server stopped")
