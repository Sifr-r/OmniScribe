"""Loader: reads ``cordis.yml``, applies patches, validates, mounts plugins.

Patch layering order: base file -> patch files (in the given order) ->
``OMNISCRIBE_PLUGIN_<ID>__<FIELD>`` env overrides. Each layer deep-merges by
row ``id`` — later fields override, missing fields are inherited, lists are
replaced. Bad config fails loud at boot, not on first request.
"""

from __future__ import annotations

import logging
import os
import sys
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

# Plugin registry: maps a cordis ``use`` string (``"module:ClassName"``) to
# a Plugin class. Built-ins are populated by :func:`_autoregister_builtin_plugins`
# at module load time using literal imports — there is no dynamic import of a
# caller-supplied module name, so the Semgrep ``non-literal-import`` rule does
# not apply. Third-party plugins register themselves via
# :func:`register_plugin` before the harness boots.
_PLUGIN_REGISTRY: dict[str, type[Plugin]] = {}

# ``_autoregister_builtin_plugins`` runs lazily on the first
# :func:`resolve_plugin` call. Running it at module-load time would
# create an import cycle: any module that imports from
# ``omniscribe.harness`` (transitively, via the package's eager
# ``__init__``) would trigger the autoload, which imports every
# built-in plugin module — including ones that themselves import
# from ``omniscribe.harness``, e.g. ``omniscribe.plugins.state_backend``
# (which subclasses ``Plugin``). With the eager autoload in place,
# importing ``state_backend`` for a class like ``MemoryStateBackend``
# would deadlock on a partially-initialised module and raise
# ``ImportError: cannot import name 'X' from partially initialized
# module 'omniscribe.plugins.state_backend'``.
_autoregistered = False


def _ensure_autoregistered() -> None:
    """Run :func:`_autoregister_builtin_plugins` exactly once.

    The guard is global so repeated :func:`resolve_plugin` calls stay
    O(1) instead of re-importing every built-in plugin module each
    time. Third-party plugins that opt in via :func:`register_plugin`
    are unaffected — their entries land in the registry independently
    of when the autoload runs.

    After the built-in autoload, :func:`_register_instance_aliases`
    scans :data:`sys.modules` for any module that re-exports a
    ``plugin = SomePlugin()`` instance (parent packages do this in
    their ``__init__`` so cordis rows can reference the shorter
    ``omniscribe.plugins.ocr:plugin`` form instead of the deeper
    ``omniscribe.plugins.ocr.plugin:plugin``). Each such alias is
    keyed under its host module, which is what the conftest and
    shipped cordis configs use interchangeably.
    """
    global _autoregistered
    if _autoregistered:
        return
    _autoregistered = True
    _autoregister_builtin_plugins()
    _register_instance_aliases()


def _register_instance_aliases() -> None:
    """Bind ``module:plugin`` aliases to the matching registered class.

    Several plugin packages (e.g. ``omniscribe.plugins.ocr``) re-export
    a ``plugin = OCRPlugin()`` instance from their ``__init__`` so
    cordis rows can use the parent module path. This pass walks every
    loaded module, looks for a ``plugin`` attribute that is an instance
    of a Plugin subclass already in the registry, and registers a
    ``f"{module.__name__}:plugin"`` alias for it. The alias is the
    shorter path the operator typically wires in ``cordis.yml``; the
    class-form key remains the source of truth for instance identity.
    """
    for module_name, module in list(sys.modules.items()):
        if module is None:
            continue
        candidate = getattr(module, "plugin", None)
        if candidate is None or isinstance(candidate, type):
            continue
        cls = type(candidate)
        if cls in _PLUGIN_REGISTRY.values() and not isinstance(candidate, type):
            _PLUGIN_REGISTRY[f"{module_name}:plugin"] = cls


def register_plugin(cls: type[Plugin]) -> type[Plugin]:
    """Decorator: opt a Plugin class into the harness plugin registry.

    Once registered, the plugin can be referenced by a cordis ``use`` string
    equal to ``f"{cls.__module__}:{cls.__qualname__}"``. A class is safe to
    register when its constructor takes no required arguments (the harness
    will instantiate it with no parameters).

    For backward compatibility with cordis configs that reference the
    module‑level ``plugin = SomePlugin()`` instance (the lowercase form,
    ``f"{cls.__module__}:plugin"``), the same class is also registered
    under that key when the host module exposes an attribute named
    ``plugin`` whose value is an instance of ``cls``. The dual entry
    means existing ``use: omniscribe.plugins.runtime:plugin``‑style
    references resolve through the registry without forcing callers to
    rename the cordis row. The class‑form key is the source of truth;
    the instance alias is a fallback so historical configs keep
    working.
    """
    key = f"{cls.__module__}:{cls.__qualname__}"
    _PLUGIN_REGISTRY[key] = cls
    module = sys.modules.get(cls.__module__)
    instance_alias = getattr(module, "plugin", None) if module is not None else None
    if isinstance(instance_alias, cls):
        # The instance form takes precedence over the class form for the
        # ``:plugin`` key — the harness Loader historically treats the
        # instance as the canonical reference and re‑uses whatever the
        # operator already wired in ``cordis.yml``.
        _PLUGIN_REGISTRY[f"{cls.__module__}:plugin"] = cls
    return cls


def _autoregister_builtin_plugins() -> None:
    """Populate :data:`_PLUGIN_REGISTRY` with every built-in Plugin class.

    Each import here is a literal module path — Semgrep's
    ``non-literal-import`` rule only fires when the argument to
    ``importlib.import_module`` is a variable, not a string literal.
    """
    from omniscribe.plugins.artifacts import ArtifactsPlugin
    from omniscribe.plugins.documents.plugin import DocumentsPlugin
    from omniscribe.plugins.glossary.plugin import GlossaryPlugin
    from omniscribe.plugins.health import HealthPlugin
    from omniscribe.plugins.jobs import JobsPlugin
    from omniscribe.plugins.logging import LoggingPlugin
    from omniscribe.plugins.ocr.plugin import OCRPlugin
    from omniscribe.plugins.progress import ProgressPlugin
    from omniscribe.plugins.providers import ProvidersPlugin
    from omniscribe.plugins.runtime import RuntimePlugin
    from omniscribe.plugins.sample_pdfs import SamplePdfsPlugin
    from omniscribe.plugins.state_backend import StateBackendPlugin
    from omniscribe.plugins.transcribe.plugin import TranscribePlugin
    from omniscribe.plugins.translate.plugin import TranslatePlugin

    for cls in (
        ArtifactsPlugin,
        DocumentsPlugin,
        GlossaryPlugin,
        HealthPlugin,
        JobsPlugin,
        LoggingPlugin,
        OCRPlugin,
        ProgressPlugin,
        ProvidersPlugin,
        RuntimePlugin,
        SamplePdfsPlugin,
        StateBackendPlugin,
        TranscribePlugin,
        TranslatePlugin,
    ):
        register_plugin(cls)


# Autoregistration is now lazy — see :func:`_ensure_autoregistered`
# above. The previous eager call to :func:`_autoregister_builtin_plugins`
# was removed because it created a circular import that aborted
# ``pytest`` collection on any test that imported
# ``omniscribe.plugins.state_backend`` directly (notably
# ``tests/api/test_channel_token_compare.py``).


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


def resolve_plugin(use: str, *, row_id: str) -> Plugin:
    """Look up a registered Plugin by ``module:ClassName`` key.

    Resolution is a plain dict lookup; no dynamic import of caller-supplied
    module names. Plugins that are not in the registry yield
    :class:`PluginLoadError` rather than being silently imported.

    For ``use: 'module:plugin'`` references that miss the registry,
    fall back to ``sys.modules[module]`` so tests can inject synthetic
    probe plugins *after* the built-in autoload has run. The alias
    walker inside ``_ensure_autoregistered`` is one-shot at module
    import, but tests often set up their fixture mid-session. The
    fallback only succeeds when the module is already in
    ``sys.modules`` and exposes a ``plugin`` attribute that is an
    instance of a Plugin class already in the registry.
    """
    if not isinstance(use, str) or ":" not in use:
        raise PluginLoadError(
            row_id=row_id, reason=f"bad 'use' path {use!r}; expected 'module:ClassName'"
        )
    _ensure_autoregistered()
    cls: type[Plugin] | None = _PLUGIN_REGISTRY.get(use)
    if cls is None:
        cls = _resolve_runtime_instance_alias(use)
    if cls is None:
        raise PluginLoadError(
            row_id=row_id,
            reason=(
                f"plugin {use!r} is not in the harness registry; "
                "register it with omniscribe.harness.loader.register_plugin"
            ),
        )
    try:
        instance: Plugin = cls()
    except Exception as exc:
        raise PluginLoadError(
            row_id=row_id,
            reason=f"cannot instantiate plugin {use!r} (id {row_id!r}): {exc}",
        ) from exc
    return instance


def _resolve_runtime_instance_alias(use: str) -> type[Plugin] | None:
    """Resolve ``module:plugin`` against ``sys.modules`` for late-bound plugins.

    Built-ins are picked up during ``_ensure_autoregistered``; this
    fallback handles modules injected into ``sys.modules`` *after* the
    autoload ran (e.g. test harnesses that wire a synthetic probe). The
    module must already be in ``sys.modules`` — we do not perform any
    dynamic import here, matching the rest of the harness's safety
    posture against caller-supplied module paths.
    """
    module_name, _, attr = use.partition(":")
    if not module_name or attr != "plugin":
        return None
    module = sys.modules.get(module_name)
    if module is None:
        return None
    candidate = getattr(module, "plugin", None)
    if candidate is None or isinstance(candidate, type):
        return None
    cls = type(candidate)
    if cls in _PLUGIN_REGISTRY.values():
        # Cache for subsequent lookups in the same session.
        _PLUGIN_REGISTRY[use] = cls
        return cls
    return None


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
        # ``resolve_plugin`` returns an instantiated Plugin (or raises
        # PluginLoadError); no further validation needed here.
        return resolve_plugin(row.use, row_id=row.id)

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
