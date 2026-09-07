"""Base configuration for text recall passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from omniscribe.utils.env import DISABLE_STRINGS, env_str


@dataclass(frozen=True, slots=True)
class BaseRecallOptions:
    """Base options for secondary text-recall passes."""

    enabled: bool = True

    @classmethod
    def _from_env(cls, env_var: str) -> Self:
        """Seed recall options from an environment variable name (default on).

        Only explicit disable values (``0``/``false``/``no``/``off``/
        ``n``/``disabled``, case-insensitive) turn the pass off; unset or
        unrecognized values keep it enabled.
        """
        raw = (env_str(env_var) or "").strip().lower()
        return cls(enabled=raw not in DISABLE_STRINGS)
