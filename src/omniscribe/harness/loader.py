"""Loader: reads ``cordis.yml``, applies patches, validates, mounts plugins.

Patch layering order: base file -> patch files (in the given order) ->
``OMNISCRIBE_PLUGIN_<ID>__<FIELD>`` env overrides. Each layer deep-merges by
row ``id`` — later fields override, missing fields are inherited, lists are
replaced. Bad config fails loud at boot, not on first request.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from omniscribe.harness.config import expand_env
from omniscribe.harness.context import Context
from omniscribe.harness.errors import PluginLoadError
from omniscribe.harness.plugin import Plugin

_LOGGER = logging.getLogger("omniscribe.harness")

_ENV_OVERRIDE_PREFIX = "OMNISCRIBE_PLUGIN_"


@dataclass(frozen=True)
class PluginRow:
    """One declared plugin in the ``cordis.yml`` tree."""

    id: str
    use: str
    config: dict[str, Any] = field(default_factory=dict)


def parse_rows(yaml_text: str) -> list[PluginRow]:
    """Parse a ``cordis.yml`` document into ``PluginRow`` entries."""
    data = yaml.safe_load(yaml_text) or {}
    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        raise PluginLoadError(
            row_id="<file>", reason="expected a top-level 'plugins' list"
        )
    rows: list[PluginRow] = []
    for index, entry in enumerate(data["plugins"]):
        if not isinstance(entry, dict):
            raise PluginLoadError(
                row_id=f"<row {index}>", reason="row must be a mapping"
            )
        row_id = entry.get("id")
        if not isinstance(row_id, str) or not row_id.strip():
            raise PluginLoadError(row_id=f"<row {index}>", reason="missing 'id'")
        use = entry.get("use")
        if not isinstance(use, str) or not use.strip():
            raise PluginLoadError(row_id=row_id, reason="missing 'use'")
        config = entry.get("config") or {}
        if not isinstance(config, dict):
            raise PluginLoadError(row_id=row_id, reason="'config' must be a mapping")
        rows.append(PluginRow(id=row_id, use=use, config=dict(config)))
    return rows


def _merge_config(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_config(existing, value)
        else:
            merged[key] = value
    return merged


def deep_merge(base: list[PluginRow], patch: list[PluginRow]) -> list[PluginRow]:
    """Merge ``patch`` rows into ``base`` keyed by ``id``.

    Base order is preserved; patch-only ids are appended. ``use`` may be
    replaced to swap the implementation itself.
    """
    by_id = {row.id: row for row in base}
    order = [row.id for row in base]
    for row in patch:
        existing = by_id.get(row.id)
        if existing is None:
            by_id[row.id] = row
            order.append(row.id)
        else:
            by_id[row.id] = PluginRow(
                id=row.id,
                use=row.use,
                config=_merge_config(existing.config, row.config),
            )
    return [by_id[row_id] for row_id in order]


def resolve_plugin(use: str, *, row_id: str) -> Any:
    """Import ``module:attr`` and return the attribute."""
    module_name, sep, attr = use.partition(":")
    if not sep or not module_name or not attr:
        raise PluginLoadError(
            row_id=row_id, reason=f"bad 'use' path {use!r}; expected 'module:attr'"
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - depends on installed tree
        raise PluginLoadError(
            row_id=row_id, reason=f"cannot import {module_name!r}: {exc}"
        ) from exc
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise PluginLoadError(
            row_id=row_id, reason=f"module {module_name!r} has no attribute {attr!r}"
        ) from exc


def _apply_env_overrides(rows: list[PluginRow]) -> list[PluginRow]:
    """Fold ``OMNISCRIBE_PLUGIN_<ID>__<FIELD>`` env vars into row configs.

    Values land as raw strings; the plugin's pydantic ``Schema`` coerces them
    to the declared field type during validation.
    """
    overrides: dict[str, dict[str, str]] = {}
    for key, raw in os.environ.items():
        if not key.startswith(_ENV_OVERRIDE_PREFIX):
            continue
        rest = key[len(_ENV_OVERRIDE_PREFIX) :]
        plugin_part, sep, field_part = rest.partition("__")
        if not sep or not plugin_part or not field_part:
            continue
        overrides.setdefault(plugin_part.lower(), {})[field_part.lower()] = raw
    if not overrides:
        return rows
    # Match case-insensitively: env keys are uppercased by convention while
    # row ids keep their cordis.yml casing, so exact matching silently
    # dropped every override for a capitalized row id (pedantic review 1.2).
    folded: list[PluginRow] = []
    for row in rows:
        row_overrides = overrides.get(row.id.lower())
        if row_overrides:
            updated_row = replace(row, config={**row.config, **row_overrides})
        else:
            updated_row = row
        folded.append(updated_row)
    return folded


class Loader:
    """Resolves, validates, and mounts the plugin tree onto a ``Context``."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def load(
        self,
        base_path: Path | str,
        *,
        patch_paths: Sequence[Path | str] = (),
    ) -> Context:
        base = Path(base_path)
        try:
            rows = parse_rows(base.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PluginLoadError(
                row_id="<file>", reason=f"cordis config not found: {base}"
            ) from exc
        for patch_path in patch_paths:
            path = Path(patch_path)
            if not path.is_file():
                env_patch = os.environ.get("OMNISCRIBE_CORDIS_PATCH", "")
                env_entries = [
                    Path(p.strip()).expanduser()
                    for p in env_patch.split(",")
                    if p.strip()
                ]
                is_explicit = any(
                    path == ep
                    or str(path) == str(ep)
                    or (
                        path.is_absolute()
                        and ep.is_absolute()
                        and path.resolve() == ep.resolve()
                    )
                    for ep in env_entries
                ) or (path.name != "cordis.patch.yml")
                if is_explicit:
                    _LOGGER.warning(
                        "Cordis patch file specified but not found: %s", path
                    )
                continue
            _LOGGER.info("Applying cordis patch: %s", path)
            rows = deep_merge(rows, parse_rows(path.read_text(encoding="utf-8")))
        rows = _apply_env_overrides(rows)

        mounted: list[str] = []
        for row in rows:
            updated_row = replace(row, config=expand_env(row.config, row_id=row.id))
            instance = self._instantiate(updated_row)
            config = self._validate(updated_row, instance)
            instance.id = updated_row.id
            try:
                await self._ctx.plugin(instance, config=config)
            except Exception as exc:
                if isinstance(exc, PluginLoadError):
                    raise
                raise PluginLoadError(row_id=updated_row.id, reason=str(exc)) from exc
            mounted.append(updated_row.id)
        _LOGGER.info(
            "harness mounted plugins: %s (%d plugins)",
            ", ".join(mounted),
            len(mounted),
        )
        return self._ctx

    def _instantiate(self, row: PluginRow) -> Plugin:
        target = resolve_plugin(row.use, row_id=row.id)
        if isinstance(target, type):
            try:
                target = target()
            except Exception as exc:
                raise PluginLoadError(
                    row_id=row.id,
                    reason=f"cannot instantiate plugin {row.use!r} (id {row.id!r}): {exc}",
                ) from exc
        if not isinstance(target, Plugin):
            raise PluginLoadError(
                row_id=row.id,
                reason=f"{row.use!r} (id {row.id!r}) is not a harness Plugin",
            )
        return target

    def _validate(self, row: PluginRow, instance: Plugin) -> dict[str, Any]:
        schema = instance.Schema
        if schema is None:
            return dict(row.config)
        try:
            return schema(**row.config).model_dump()
        except ValidationError as exc:
            raise PluginLoadError(
                row_id=row.id, reason=f"invalid config: {exc}"
            ) from exc
