"""Exception class tests."""

from __future__ import annotations

import pytest

from moomoo_bot.exceptions import (
    MoomooBotError,
    ConfigurationError,
    BrokerConnectionError,
    RiskHaltError,
    OrderRejectedError,
    DataError,
    OrderTimeoutError,
)


class TestMoomooBotError:
    """Tests for MoomooBotError base exception."""
    
    def test_base_exception_creation(self) -> None:
        """Test that base exception can be created."""
        exc = MoomooBotError("Test error")
        assert str(exc) == "Test error"

    def test_base_exception_is_exception(self) -> None:
        """Test that MoomooBotError is an Exception."""
        exc = MoomooBotError("Test")
        assert isinstance(exc, Exception)

    def test_base_exception_can_be_raised(self) -> None:
        """Test that base exception can be raised and caught."""
        with pytest.raises(MoomooBotError):
            raise MoomooBotError("Test error")

    def test_base_exception_message_preserved(self) -> None:
        """Test that exception message is preserved."""
        message = "This is a test error"
        try:
            raise MoomooBotError(message)
        except MoomooBotError as e:
            assert str(e) == message


class TestConfigurationError:
    """Tests for ConfigurationError."""
    
    def test_configuration_error_creation(self) -> None:
        exc = ConfigurationError("Invalid config")
        assert str(exc) == "Invalid config"

    def test_configuration_error_inherits_from_base(self) -> None:
        exc = ConfigurationError("Invalid config")
        assert isinstance(exc, MoomooBotError)
        assert isinstance(exc, Exception)

    def test_configuration_error_can_be_caught_as_base(self) -> None:
        with pytest.raises(MoomooBotError):
            raise ConfigurationError("Invalid config")

    def test_configuration_error_preserves_message(self) -> None:
        message = "Missing MOOMOO_BOT_STATE_DB_PATH"
        try:
            raise ConfigurationError(message)
        except ConfigurationError as e:
            assert str(e) == message


class TestBrokerConnectionError:
    """Tests for BrokerConnectionError."""
    
    def test_broker_connection_error_creation(self) -> None:
        exc = BrokerConnectionError("Connection refused")
        assert str(exc) == "Connection refused"

    def test_broker_connection_error_inherits_from_base(self) -> None:
        exc = BrokerConnectionError("Connection refused")
        assert isinstance(exc, MoomooBotError)

    def test_broker_connection_error_can_be_caught(self) -> None:
        with pytest.raises(BrokerConnectionError):
            raise BrokerConnectionError("Connection timeout")


class TestRiskHaltError:
    """Tests for RiskHaltError."""
    
    def test_risk_halt_error_creation(self) -> None:
        exc = RiskHaltError("Max drawdown exceeded")
        assert str(exc) == "Max drawdown exceeded"

    def test_risk_halt_error_inherits_from_base(self) -> None:
        exc = RiskHaltError("Max drawdown exceeded")
        assert isinstance(exc, MoomooBotError)

    def test_risk_halt_error_can_be_caught(self) -> None:
        with pytest.raises(RiskHaltError):
            raise RiskHaltError("Daily loss limit hit")

    def test_risk_halt_error_preservation(self) -> None:
        message = "Equity dropped 25%"
        try:
            raise RiskHaltError(message)
        except RiskHaltError as e:
            assert str(e) == message


class TestOrderRejectedError:
    """Tests for OrderRejectedError."""
    
    def test_order_rejected_error_creation(self) -> None:
        exc = OrderRejectedError("Order rejected by broker")
        assert str(exc) == "Order rejected by broker"

    def test_order_rejected_error_inherits_from_base(self) -> None:
        exc = OrderRejectedError("Order rejected by broker")
        assert isinstance(exc, MoomooBotError)

    def test_order_rejected_error_can_be_caught(self) -> None:
        with pytest.raises(OrderRejectedError):
            raise OrderRejectedError("Insufficient buying power")

    def test_order_rejected_error_message_variations(self) -> None:
        messages = [
            "Insufficient buying power",
            "Invalid quantity",
            "Order size too small",
            "Market closed",
        ]
        
        for message in messages:
            try:
                raise OrderRejectedError(message)
            except OrderRejectedError as e:
                assert str(e) == message


class TestDataError:
    """Tests for DataError."""
    
    def test_data_error_creation(self) -> None:
        exc = DataError("Missing price data")
        assert str(exc) == "Missing price data"

    def test_data_error_inherits_from_base(self) -> None:
        exc = DataError("Missing price data")
        assert isinstance(exc, MoomooBotError)

    def test_data_error_can_be_caught(self) -> None:
        with pytest.raises(DataError):
            raise DataError("Invalid historical data")

    def test_data_error_message_preservation(self) -> None:
        message = "No data available for AAPL from 2020-01-01"
        try:
            raise DataError(message)
        except DataError as e:
            assert str(e) == message


class TestOrderTimeoutError:
    """Tests for OrderTimeoutError."""
    
    def test_order_timeout_error_creation(self) -> None:
        exc = OrderTimeoutError("Order submission timed out")
        assert str(exc) == "Order submission timed out"

    def test_order_timeout_error_inherits_from_base(self) -> None:
        exc = OrderTimeoutError("Order submission timed out")
        assert isinstance(exc, MoomooBotError)

    def test_order_timeout_error_can_be_caught(self) -> None:
        with pytest.raises(OrderTimeoutError):
            raise OrderTimeoutError("Broker API timeout after 30s")

    def test_order_timeout_error_message_preserved(self) -> None:
        message = "No response from broker after 60 seconds"
        try:
            raise OrderTimeoutError(message)
        except OrderTimeoutError as e:
            assert str(e) == message


class TestExceptionHierarchy:
    """Tests for exception inheritance hierarchy."""
    
    def test_all_custom_exceptions_inherit_from_base(self) -> None:
        """Test that all custom exceptions inherit from MoomooBotError."""
        exceptions = [
            ConfigurationError("test"),
            BrokerConnectionError("test"),
            RiskHaltError("test"),
            OrderRejectedError("test"),
            DataError("test"),
            OrderTimeoutError("test"),
        ]
        
        for exc in exceptions:
            assert isinstance(exc, MoomooBotError)
            assert isinstance(exc, Exception)

    def test_exception_can_be_caught_by_base_class(self) -> None:
        """Test that specific exceptions can be caught by base class."""
        exceptions_to_raise = [
            ConfigurationError("config"),
            BrokerConnectionError("broker"),
            RiskHaltError("risk"),
            OrderRejectedError("rejected"),
            DataError("data"),
            OrderTimeoutError("timeout"),
        ]
        
        for exc_to_raise in exceptions_to_raise:
            with pytest.raises(MoomooBotError):
                raise exc_to_raise

    def test_exception_args_preserved(self) -> None:
        """Test that exception args are preserved."""
        exc = ConfigurationError("arg1", "arg2")
        assert exc.args == ("arg1", "arg2")

    def test_exception_with_no_args(self) -> None:
        """Test exception creation with no args."""
        exc = MoomooBotError()
        assert exc.args == ()
        assert str(exc) == ""
