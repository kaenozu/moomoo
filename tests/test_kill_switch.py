"""Tests for kill switch module."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from moomoo_bot.kill_switch import (
    is_kill_switch_active,
    activate_kill_switch,
    deactivate_kill_switch,
    kill_switch_path,
)


class TestKillSwitchPath:
    def test_kill_switch_path_returns_path(self):
        path = kill_switch_path()
        assert isinstance(path, Path)
        assert "KILL_SWITCH" in str(path)
        assert ".moomoo_bot" in str(path)


class TestIsKillSwitchActive:
    def test_no_kill_switch_file_returns_false(self, tmp_path):
        with patch(
            "moomoo_bot.kill_switch._KILL_SWITCH_FILE", tmp_path / "KILL_SWITCH"
        ):
            assert is_kill_switch_active() is False

    def test_kill_switch_file_exists_returns_true(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        kill_file.write_text("ACTIVE\n")
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            assert is_kill_switch_active() is True

    def test_kill_switch_file_empty_returns_true(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        kill_file.touch()
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            assert is_kill_switch_active() is True


class TestActivateKillSwitch:
    def test_activate_creates_file(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            activate_kill_switch()
            assert kill_file.exists()
            content = kill_file.read_text()
            assert "ACTIVE" in content

    def test_activate_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "KILL_SWITCH"
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", nested):
            activate_kill_switch()
            assert nested.exists()
            assert nested.parent.exists()

    def test_activate_overwrites_existing(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        kill_file.write_text("OLD_CONTENT")
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            activate_kill_switch()
            content = kill_file.read_text()
            assert "ACTIVE" in content
            assert "OLD_CONTENT" not in content


class TestDeactivateKillSwitch:
    def test_deactivate_removes_existing_file(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        kill_file.write_text("ACTIVE\n")
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            deactivate_kill_switch()
            assert not kill_file.exists()

    def test_deactivate_no_file_is_safe(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            # Should not raise any exception
            deactivate_kill_switch()
            assert not kill_file.exists()

    def test_deactivate_only_removes_kill_switch(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        other_file = tmp_path / "OTHER_FILE"
        kill_file.write_text("ACTIVE\n")
        other_file.write_text("keep me")
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            deactivate_kill_switch()
            assert not kill_file.exists()
            assert other_file.exists()


class TestKillSwitchIntegration:
    def test_full_cycle(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            # Initially no file
            assert is_kill_switch_active() is False

            # Activate
            activate_kill_switch()
            assert is_kill_switch_active() is True
            assert kill_file.exists()

            # Deactivate
            deactivate_kill_switch()
            assert is_kill_switch_active() is False
            assert not kill_file.exists()

    def test_multiple_activations(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            activate_kill_switch()
            first_content = kill_file.read_text()
            activate_kill_switch()
            second_content = kill_file.read_text()
            assert first_content == second_content


class TestKillSwitchContent:
    def test_content_format(self, tmp_path):
        kill_file = tmp_path / "KILL_SWITCH"
        with patch("moomoo_bot.kill_switch._KILL_SWITCH_FILE", kill_file):
            activate_kill_switch()
            content = kill_file.read_text()
            assert content.startswith("ACTIVE")
            assert "\n" in content or content.endswith("\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
