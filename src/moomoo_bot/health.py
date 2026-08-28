"""Health check module.

Purpose: Simple HTTP health check endpoint for monitoring.
Related: orchestrator.py, broker modules.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Lock, Thread


logger = logging.getLogger(__name__)

# Optional bearer token for health endpoint auth
_HEALTH_AUTH_TOKEN = os.getenv("MOOMOO_BOT_HEALTH_TOKEN", "")


def _host_is_loopback(host: str) -> bool:
    """Return True only when every resolved bind address is loopback."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return literal.is_loopback

    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                None,
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror:
        # An unresolved hostname is not proven local; require authentication.
        return False
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_loopback for address in addresses)
    except ValueError:
        return False


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
        # Token auth if configured
        if _HEALTH_AUTH_TOKEN:
            auth_header = self.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                self.send_response(401)
                self.end_headers()
                return
            token = auth_header.split("Bearer ", 1)[1].strip()
            if token != _HEALTH_AUTH_TOKEN:
                self.send_response(403)
                self.end_headers()
                return

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
            snapshot = replace(self._status)
            snapshot.uptime_seconds = (
                datetime.now(timezone.utc) - self._start_time
            ).total_seconds()
            return snapshot

    def start(self):
        """Start the health check server in a background thread."""
        if self.server is not None:
            logger.warning("Health check server already running")
            return

        host = os.getenv("MOOMOO_BOT_HEALTH_HOST", "127.0.0.1")
        if not _host_is_loopback(host) and not _HEALTH_AUTH_TOKEN:
            logger.error(
                "Refusing to bind health endpoint to non-loopback host %s without "
                "MOOMOO_BOT_HEALTH_TOKEN.",
                host,
            )
            raise RuntimeError(
                "Health check server requires MOOMOO_BOT_HEALTH_TOKEN for every non-loopback bind"
            )

        def handler(*args, **kwargs):
            return HealthRequestHandler(*args, status_getter=self._get_status, **kwargs)

        logger.info("Starting health check server on %s:%d", host, self.port)
        self.server = HTTPServer((host, self.port), handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info("Health check server started on %s:%d", host, self.port)

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
