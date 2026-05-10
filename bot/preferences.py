"""Persist Telegram-controlled filter preferences to settings.yaml.

The bot must mutate `filters.min_severity` and `filters.enabled_types` in
response to inline-keyboard taps. The on-disk YAML still uses `${VAR}`
placeholders for secrets, so we never dump the resolved settings dict —
we always re-read the raw YAML, mutate only the `filters` subtree, and
write atomically (tmp file + os.replace).

WARNING: save_filters() performs a FULL REPLACEMENT of the `filters` subtree.
Callers must pass the complete desired filters dict (including quiet_hours, etc.),
not just the keys they want to change. Always pass context.bot_data["settings"]["filters"].
YAML comments in settings.yaml are not preserved across writes — do not add
inline comments to the filters section.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def load_raw_settings(path: Path) -> dict[str, Any]:
    """Return the YAML as-is, with `${VAR}` placeholders intact."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_filters(path: Path, filters: dict[str, Any]) -> None:
    """Atomically rewrite settings.yaml, replacing only the `filters` subtree.

    Reads the raw YAML (placeholders intact), swaps in the new filters dict,
    writes to a sibling tempfile, then atomically renames over the original.
    """
    raw = load_raw_settings(path)
    raw["filters"] = filters

    parent = path.parent
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        logger.info("Saved filter preferences to %s", path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
