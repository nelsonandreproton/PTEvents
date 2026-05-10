"""Tests for bot.preferences — atomic YAML write that preserves secrets."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from bot.preferences import load_raw_settings, save_filters


SAMPLE_YAML = """\
location:
  lat: 38.8097
  lon: -9.2518
  radius_km: 5
  name: "Casa"

telegram:
  token: "${TELEGRAM_BOT_TOKEN}"
  chat_id: "${TELEGRAM_CHAT_ID}"

filters:
  min_severity: LOW
  quiet_hours:
    enabled: true
    start: "23:00"
    end: "07:00"
    except_severity: CRITICAL

api_keys:
  here: "${HERE_API_KEY}"
  tomtom: "${TOMTOM_API_KEY}"
"""


@pytest.fixture
def settings_file():
    tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8")
    tmp.write(SAMPLE_YAML)
    tmp.close()
    path = Path(tmp.name)
    yield path
    try:
        os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass


def test_load_raw_keeps_placeholders_intact(settings_file):
    raw = load_raw_settings(settings_file)
    assert raw["telegram"]["token"] == "${TELEGRAM_BOT_TOKEN}"
    assert raw["api_keys"]["here"] == "${HERE_API_KEY}"


def test_save_filters_preserves_secret_placeholders(settings_file):
    new_filters = {
        "min_severity": "HIGH",
        "enabled_types": ["FIRE", "ACCIDENT"],
    }
    save_filters(settings_file, new_filters)

    reloaded = load_raw_settings(settings_file)
    assert reloaded["telegram"]["token"] == "${TELEGRAM_BOT_TOKEN}"
    assert reloaded["telegram"]["chat_id"] == "${TELEGRAM_CHAT_ID}"
    assert reloaded["api_keys"]["here"] == "${HERE_API_KEY}"
    assert reloaded["api_keys"]["tomtom"] == "${TOMTOM_API_KEY}"


def test_save_filters_replaces_filter_subtree(settings_file):
    new_filters = {
        "min_severity": "CRITICAL",
        "enabled_types": ["FIRE"],
    }
    save_filters(settings_file, new_filters)

    reloaded = load_raw_settings(settings_file)
    assert reloaded["filters"] == new_filters
    # quiet_hours from the original must be gone — caller is responsible
    # for passing the full filters dict it wants persisted
    assert "quiet_hours" not in reloaded["filters"]


def test_save_filters_keeps_other_top_level_keys(settings_file):
    save_filters(settings_file, {"min_severity": "LOW", "enabled_types": None})

    reloaded = load_raw_settings(settings_file)
    assert reloaded["location"]["radius_km"] == 5
    assert reloaded["location"]["name"] == "Casa"


def test_save_filters_atomic_no_leftover_tmp(settings_file):
    save_filters(settings_file, {"min_severity": "HIGH"})

    parent = settings_file.parent
    leftovers = [p for p in parent.iterdir() if p.name.startswith(settings_file.name + ".") and p.suffix == ".tmp"]
    assert leftovers == []


def test_save_filters_writes_valid_yaml(settings_file):
    save_filters(settings_file, {"min_severity": "MEDIUM", "enabled_types": ["FIRE", "STORM"]})

    # Must be parseable
    with open(settings_file, "r", encoding="utf-8") as f:
        parsed = yaml.safe_load(f)

    assert parsed["filters"]["min_severity"] == "MEDIUM"
    assert parsed["filters"]["enabled_types"] == ["FIRE", "STORM"]
