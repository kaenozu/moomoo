"""Custom exceptions module.

Purpose: Define domain-specific exceptions for moomoo-bot.
Related: All modules that raise errors.
"""


class MoomooBotError(Exception):
    """Base exception for all moomoo-bot errors."""

    pass


class ConfigurationError(MoomooBotError):
    """Raised when configuration is invalid or missing."""

    pass


class BrokerConnectionError(MoomooBotError):
    """Raised when broker connection fails."""

    pass


class RiskHaltError(MoomooBotError):
    """Raised when trading is halted due to risk rules."""

    pass


class OrderRejectedError(MoomooBotError):
    """Raised when order submission is rejected."""

    pass


class DataError(MoomooBotError):
    """Raised when data is missing or invalid."""

    pass


class OrderTimeoutError(MoomooBotError):
    """Raised when an order request times out."""

    pass
