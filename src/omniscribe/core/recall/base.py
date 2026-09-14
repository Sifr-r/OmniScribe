"""Base configuration for text recall passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Self

from omniscribe.utils.env import DISABLE_STRINGS, env_str


@dataclass(frozen=True, slots=True)
class BaseRecallOptions:
    """Base options for secondary text-recall passes.

    Subclasses set the :attr:`env_var` class attribute to the env var that
    toggles them and inherit :meth:`from_env` unchanged. The previous pattern
    was a byte-identical ``from_env`` method on every subclass
    (``WhitespaceRecallOptions``, ``TextLayerRecallOptions``) that each
    hard-coded one env-var name (audit F12).
    """

    #: Env-var name that toggles the pass. Subclasses override.
    env_var: ClassVar[str] = ""

    enabled: bool = True

    @classmethod
    def from_env(cls) -> Self:
        """Seed options from :attr:`env_var` (default on).

        Only explicit disable values (``0``/``false``/``no``/``off``/
        ``n``/``disabled``, case-insensitive) turn the pass off; unset or
        unrecognized values keep it enabled.
        """
        raw = (env_str(cls.env_var) or "").strip().lower()
        return cls(enabled=raw not in DISABLE_STRINGS)
