"""Plugin base class for the harness.

A plugin is a plain class with an ``id``, an optional pydantic ``Schema`` for
its config, and async ``apply`` / ``dispose`` hooks. The loader sets ``id``
and ``config`` before ``Context.plugin`` calls ``apply``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from omniscribe.harness.context import Context


class EmptySchema(BaseModel):
    """Sentinel :class:`pydantic.BaseModel` for plugins with no configuration.

    Replaces the byte-identical ``class XSchema(BaseModel): 'No configurable
    fields.'`` placeholders that used to live in every plugin's ``plugin.py``
    (audit F5). Point a plugin's ``Schema`` at this class to declare it takes
    no user-facing configuration.
    """


class Plugin:
    """Base class for every harness plugin."""

    id: str = ""
    Schema: ClassVar[type[BaseModel] | None] = None

    def __init__(self) -> None:
        self.config: dict[str, Any] = {}

    async def apply(self, ctx: Context) -> None:
        """Register services, effects, listeners, and routers on ``ctx``."""

    async def dispose(self) -> None:
        """Optional teardown after the context reversed this plugin's effects."""
