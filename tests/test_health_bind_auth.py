"""Regression tests for health endpoint bind authentication."""

from __future__ import annotations

import pytest

import moomoo_bot.health as health


def test_loopback_literals_are_local() -> None:
    assert health._host_is_loopback("127.0.0.1") is True
    assert health._host_is_loopback("127.12.34.56") is True
    assert health._host_is_loopback("::1") is True
    assert health._host_is_loopback("0.0.0.0") is False
    assert health._host_is_loopback("192.168.1.10") is False


def test_hostname_is_local_only_when_every_resolution_is_loopback(monkeypatch) -> None:
    monkeypatch.setattr(
        health.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("127.0.0.1", 0)),
            (None, None, None, None, ("::1", 0, 0, 0)),
        ],
    )
    assert health._host_is_loopback("localhost.example") is True

    monkeypatch.setattr(
        health.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("127.0.0.1", 0)),
            (None, None, None, None, ("192.168.1.20", 0)),
        ],
    )
    assert health._host_is_loopback("mixed.example") is False


def test_non_loopback_bind_without_token_fails_before_http_server(monkeypatch) -> None:
    monkeypatch.setenv("MOOMOO_BOT_HEALTH_HOST", "192.168.1.10")
    monkeypatch.setattr(health, "_HEALTH_AUTH_TOKEN", "")
    server = health.HealthCheckServer(port=18099)

    with pytest.raises(RuntimeError, match="non-loopback"):
        server.start()

    assert server.server is None
    assert server.thread is None
