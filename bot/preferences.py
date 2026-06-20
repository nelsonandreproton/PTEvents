"""Runtime filter overrides — persisted outside the git-tracked settings.yaml.

`config/settings.yaml` is git-tracked AND bind-mounted from the repo dir, so the
homeserver `deploy.sh` (which runs `git reset --hard origin/main`) wipes any
runtime edits to it. Telegram-controlled filter selections must therefore live
in the data volume instead, where neither `git reset` nor `docker compose
--build` can touch them.

This module owns `data/filter_prefs.yaml`: a tiny standalone file holding ONLY
the Telegram-mutable keys (`min_severity`, `enabled_types`). It is layered on
top of the base `settings["filters"]` at startup via `apply_overrides`. Keys
not in OVERRIDE_KEYS (e.g. `quiet_hours`) always come from the tracked base, so
deploy-time edits to them still propagate.

The overrides file contains no secrets, so — unlike the old settings.yaml
writer — we dump it directly without `${VAR}` placeholder gymnastics.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# The only filter keys the Telegram UI / REST API may mutate at runtime.
OVERRIDE_KEYS: frozenset[str] = frozenset({"min_severity", "enabled_types"})


def load_filter_overrides(path: Path) -> dict[str, Any]:
    """Return the runtime filter overrides, or {} if the file is absent/empty.

    Only recognised override keys are returned; any stray keys in the file are
    ignored so a malformed/old file can never leak non-override settings.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as exc:
        # A corrupt/unreadable file lives in the persistent volume — never let
        # it crash-loop the container. Fall back to base settings instead.
        logger.warning("Ignoring unreadable filter overrides at %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("Ignoring malformed filter overrides at %s (not a mapping)", path)
        return {}
    return {k: v for k, v in data.items() if k in OVERRIDE_KEYS}


def save_filter_overrides(path: Path, filters: dict[str, Any]) -> None:
    """Atomically persist only the override keys from `filters` to `path`.

    Accepts a full filters dict for convenience (the API hands us the whole
    in-memory subtree); non-override keys are dropped before writing. Creates
    the parent directory if needed (first boot on a fresh data volume).
    """
    overrides = {k: filters[k] for k in OVERRIDE_KEYS if k in filters}

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(overrides, f, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        logger.info("Saved filter overrides to %s", path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def apply_overrides(base_filters: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Return a new filters dict: base with override keys replaced.

    Does not mutate `base_filters`. Only OVERRIDE_KEYS present in `overrides`
    are applied; everything else (e.g. quiet_hours) is taken from the base.
    """
    merged = dict(base_filters)
    for key in OVERRIDE_KEYS:
        if key in overrides:
            merged[key] = overrides[key]
    return merged
