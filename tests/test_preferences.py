"""Tests for bot.preferences — runtime filter overrides persisted outside the
git-tracked, bind-mounted settings.yaml so they survive `git reset --hard`.

The overrides file holds ONLY the Telegram-mutable keys (min_severity,
enabled_types). It lives in the data volume (data/filter_prefs.yaml), not in
config/, so deploy.sh's `git reset --hard origin/main` never touches it.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from bot.preferences import (
    OVERRIDE_KEYS,
    apply_overrides,
    load_filter_overrides,
    save_filter_overrides,
)


@pytest.fixture
def prefs_path():
    """A path inside a temp dir that does NOT yet exist (mirrors first boot)."""
    tmpdir = tempfile.mkdtemp()
    path = Path(tmpdir) / "filter_prefs.yaml"
    yield path
    try:
        if path.exists():
            os.unlink(path)
    except (PermissionError, FileNotFoundError):
        pass
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass


def test_load_missing_file_returns_empty(prefs_path):
    assert not prefs_path.exists()
    assert load_filter_overrides(prefs_path) == {}


def test_load_corrupt_yaml_returns_empty(prefs_path):
    # A corrupt file in the persistent volume must NOT crash-loop the container.
    prefs_path.write_text("min_severity: HIGH\n  : : bad indent :\n", encoding="utf-8")
    assert load_filter_overrides(prefs_path) == {}


def test_load_non_mapping_returns_empty(prefs_path):
    prefs_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert load_filter_overrides(prefs_path) == {}


def test_save_then_load_roundtrip(prefs_path):
    save_filter_overrides(prefs_path, {"min_severity": "HIGH", "enabled_types": ["FIRE", "ACCIDENT"]})
    loaded = load_filter_overrides(prefs_path)
    assert loaded == {"min_severity": "HIGH", "enabled_types": ["FIRE", "ACCIDENT"]}


def test_save_creates_parent_dir(prefs_path):
    nested = prefs_path.parent / "sub" / "filter_prefs.yaml"
    save_filter_overrides(nested, {"min_severity": "LOW"})
    assert nested.exists()
    assert load_filter_overrides(nested) == {"min_severity": "LOW"}


def test_save_persists_only_override_keys(prefs_path):
    # Caller may pass a full filters dict; only the override keys are stored.
    save_filter_overrides(prefs_path, {
        "min_severity": "MEDIUM",
        "enabled_types": ["STORM"],
        "quiet_hours": {"enabled": True},  # must be dropped
    })
    loaded = load_filter_overrides(prefs_path)
    assert "quiet_hours" not in loaded
    assert set(loaded) <= OVERRIDE_KEYS


def test_save_preserves_enabled_types_none(prefs_path):
    save_filter_overrides(prefs_path, {"min_severity": "LOW", "enabled_types": None})
    loaded = load_filter_overrides(prefs_path)
    assert loaded["enabled_types"] is None


def test_save_is_atomic_no_leftover_tmp(prefs_path):
    save_filter_overrides(prefs_path, {"min_severity": "HIGH"})
    leftovers = [
        p for p in prefs_path.parent.iterdir()
        if p.name.startswith(prefs_path.name + ".") and p.suffix == ".tmp"
    ]
    assert leftovers == []


def test_save_writes_valid_yaml(prefs_path):
    save_filter_overrides(prefs_path, {"min_severity": "MEDIUM", "enabled_types": ["FIRE", "STORM"]})
    with open(prefs_path, "r", encoding="utf-8") as f:
        parsed = yaml.safe_load(f)
    assert parsed["min_severity"] == "MEDIUM"
    assert parsed["enabled_types"] == ["FIRE", "STORM"]


# --- apply_overrides: layer overrides on top of a base filters dict ---

def test_apply_overrides_empty_returns_base_unchanged():
    base = {"min_severity": "LOW", "enabled_types": None, "quiet_hours": {"enabled": True}}
    merged = apply_overrides(base, {})
    assert merged == base


def test_apply_overrides_replaces_only_override_keys():
    base = {"min_severity": "LOW", "enabled_types": None, "quiet_hours": {"enabled": True}}
    merged = apply_overrides(base, {"min_severity": "HIGH", "enabled_types": ["FIRE"]})
    assert merged["min_severity"] == "HIGH"
    assert merged["enabled_types"] == ["FIRE"]
    # quiet_hours from base must survive — deploy edits to it still propagate
    assert merged["quiet_hours"] == {"enabled": True}


def test_apply_overrides_does_not_mutate_base():
    base = {"min_severity": "LOW", "enabled_types": None}
    apply_overrides(base, {"min_severity": "CRITICAL"})
    assert base["min_severity"] == "LOW"  # base untouched


def test_apply_overrides_ignores_non_override_keys():
    base = {"min_severity": "LOW", "quiet_hours": {"enabled": True}}
    # A malformed/old overrides file with extra keys must not leak them in
    merged = apply_overrides(base, {"min_severity": "HIGH", "quiet_hours": {"enabled": False}})
    assert merged["quiet_hours"] == {"enabled": True}
