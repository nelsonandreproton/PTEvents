"""Tests for bot.main: log hygiene and env placeholder substitution."""
import logging

import bot.main as main_mod
from bot.main import _substitute_env


def test_httpx_loggers_not_verbose_so_api_keys_stay_out_of_logs():
    for name in ("httpx", "httpcore"):
        # explicit level: must not depend on whatever the root logger is set to
        assert logging.getLogger(name).level >= logging.WARNING


def test_substitute_env_set_var_returns_value(monkeypatch):
    monkeypatch.setenv("PTE_TEST_KEY", "abc123")
    assert _substitute_env("${PTE_TEST_KEY}") == "abc123"


def test_substitute_env_unset_var_returns_empty_and_warns_with_name(monkeypatch, caplog):
    monkeypatch.delenv("PTE_TEST_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger=main_mod.logger.name):
        assert _substitute_env("${PTE_TEST_KEY}") == ""
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "PTE_TEST_KEY" in warnings[0].getMessage()


def test_substitute_env_empty_var_returns_empty_and_warns(monkeypatch, caplog):
    monkeypatch.setenv("PTE_TEST_KEY", "")
    with caplog.at_level(logging.WARNING, logger=main_mod.logger.name):
        assert _substitute_env("${PTE_TEST_KEY}") == ""
    assert any("PTE_TEST_KEY" in r.getMessage() for r in caplog.records)


def test_substitute_env_plain_string_unchanged(caplog):
    with caplog.at_level(logging.WARNING):
        assert _substitute_env("hello") == "hello"
        assert _substitute_env("prefix-${X}") == "prefix-${X}"
    assert caplog.records == []
