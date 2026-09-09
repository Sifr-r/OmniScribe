"""
Typed environment-variable helpers.

The dotted-string ``python-dotenv`` loader lives in the ``dotenv`` package
itself; OmniScribe does not reimplement it. ``load_settings`` (pydantic
settings) and these per-call helpers are the two supported ways to read
env vars.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

# Standard falsy / disable string values recognized across configuration and env checks.
__all__ = [
    "DISABLE_STRINGS",
    "ENABLE_STRINGS",
    "env_bool",
    "env_int",
    "env_list_csv",
    "env_str",
    "parse_bool",
    "persist_env_key",
]

logger = logging.getLogger(__name__)
_LOGGER = logger

ENABLE_STRINGS: Final[frozenset[str]] = frozenset(
    {"1", "true", "yes", "on", "y", "enabled"}
)
DISABLE_STRINGS: Final[frozenset[str]] = frozenset(
    {"0", "false", "no", "off", "n", "disabled"}
)


def env_str(name: str) -> str | None:
    """Read a trimmed string env var. None if unset or empty after strip.

    Empty-value semantics (smell 4.28):
        - Unset env var yields ``None``.
        - Empty string ``""`` (or whitespace-only) yields ``None``.
    Compare with :func:`env_list_csv`, which yields ``[]`` in both cases.
    """
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def env_int(name: str, default: int) -> int:
    """Read an integer env var with fallback and warning on invalid format."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        logger.warning("Ignoring invalid integer environment value for %s", name)
        return default


def parse_bool(value: Any, default: bool = False) -> bool:
    """Parse a boolean or string into a bool using ENABLE_STRINGS / DISABLE_STRINGS."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    val_str = str(value).strip().lower()
    if val_str in ENABLE_STRINGS:
        return True
    if val_str in DISABLE_STRINGS:
        return False
    return default


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var with canonical truthy/falsy conversion.

    If the variable is set but its stripped value is neither truthy
    (:data:`ENABLE_STRINGS`) nor falsy (:data:`DISABLE_STRINGS`), logs a warning
    and returns ``default`` (matching :func:`env_int`).
    """
    value = os.getenv(name)
    if value is None:
        return default
    val_str = value.strip().lower()
    if val_str in ENABLE_STRINGS:
        return True
    if val_str in DISABLE_STRINGS:
        return False
    logger.warning("Ignoring invalid boolean environment value for %s", name)
    return default


def env_list_csv(name: str) -> list[str]:
    """Read a comma-separated list env var. Trims each item; drops empties.

    Empty-value semantics (smell 4.28):
        - Unset env var yields ``[]``.
        - Empty string ``""`` (or whitespace-only) yields ``[]``.
    Compare with :func:`env_str`, which yields ``None`` in both cases.
    """
    raw = os.getenv(name)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def persist_env_key(key: str, value: str) -> None:
    """Persist key-value to .env in the project root safely."""
    if not isinstance(key, str) or not key.strip():
        _LOGGER.warning("Failed to persist %s to .env: key must be a non-empty string", key)
        return

    sanitized_key = key.strip()
    sanitized_val = str(value) if not isinstance(value, str) else value
    try:
        from pathlib import Path

        import dotenv

        dotenv_path = Path(".env")
        if not dotenv_path.exists():
            dotenv_path.touch()
        dotenv.set_key(str(dotenv_path), sanitized_key, sanitized_val)
    except Exception as exc:
        _LOGGER.warning("Failed to persist %s to .env: %s", sanitized_key, exc)

