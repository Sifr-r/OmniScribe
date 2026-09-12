"""Loader: YAML parsing, patch merging, env overrides, schema validation, mount."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from omniscribe.harness.context import Context
from omniscribe.harness.errors import PluginLoadError
from omniscribe.harness.loader import (
    Loader,
    PluginRow,
    deep_merge,
    parse_rows,
    register_plugin,
    resolve_plugin,
)
from omniscribe.harness.plugin import Plugin

# Pytest imports this module as ``harness.test_loader`` (no ``tests/__init__.py``);
# ``cls.__module__`` is therefore ``harness.test_loader``, and the registry
# keys are ``harness.test_loader:X``. The ``use:`` strings below match that
# form -- do not reintroduce the ``harness.test_loader:`` prefix here.
#
# (The earlier ``sys.modules.setdefault`` workaround didn't actually help:
# it made the module object reachable from the ``tests.`` path but didn't
# change ``cls.__module__``, so the registry keys still didn't match the
# loader's lookup keys.)


MOUNT_ORDER: list[str] = []


class AlphaService:
    def __init__(self, greeting: str, count: int) -> None:
        self.greeting = greeting
        self.count = count


class AlphaSchema(BaseModel):
    greeting: str = "hi"
    count: int = 1


@register_plugin
class AlphaPlugin(Plugin):
    Schema = AlphaSchema

    async def apply(self, ctx: Context) -> None:
        MOUNT_ORDER.append("alpha")
        ctx.service(
            AlphaService,
            AlphaService(
                self.config.get("greeting", "hi"), self.config.get("count", 1)
            ),
        )


@register_plugin
class BetaPlugin(Plugin):
    async def apply(self, ctx: Context) -> None:
        MOUNT_ORDER.append("beta")


_BASE_YAML = """
plugins:
  - id: alpha
    use: harness.test_loader:AlphaPlugin
    config:
      greeting: hello
  - id: beta
    use: harness.test_loader:BetaPlugin
"""


@pytest.fixture(autouse=True)
def _clear_mount_order() -> None:
    MOUNT_ORDER.clear()


# -- parse_rows -----------------------------------------------------------------


def test_parse_rows_valid() -> None:
    rows = parse_rows(_BASE_YAML)
    assert [row.id for row in rows] == ["alpha", "beta"]
    assert rows[0].use == "harness.test_loader:AlphaPlugin"
    assert rows[0].config == {"greeting": "hello"}
    assert rows[1].config == {}


def test_parse_rows_missing_id_fails() -> None:
    with pytest.raises(PluginLoadError):
        parse_rows("plugins:\n  - use: some.module:attr\n")


def test_parse_rows_missing_use_fails() -> None:
    with pytest.raises(PluginLoadError):
        parse_rows("plugins:\n  - id: a\n")


def test_parse_rows_bad_shape_fails() -> None:
    with pytest.raises(PluginLoadError):
        parse_rows("plugins:\n  not-a-list\n")


# -- deep_merge -------------------------------------------------------------------


def test_deep_merge_overrides_and_inherits() -> None:
    base = [
        PluginRow(id="a", use="m:A", config={"x": 1, "y": {"z": 2, "w": 3}}),
        PluginRow(id="b", use="m:B", config={}),
    ]
    patch = [
        PluginRow(id="a", use="m:A", config={"x": 10, "y": {"z": 20}, "list": [1]}),
        PluginRow(id="c", use="m:C", config={"new": True}),
    ]
    merged = deep_merge(base, patch)
    assert [row.id for row in merged] == ["a", "b", "c"]
    a = merged[0]
    assert a.config["x"] == 10
    assert a.config["y"] == {"z": 20, "w": 3}  # nested deep-merge
    assert a.config["list"] == [1]
    assert merged[1].use == "m:B"
    assert merged[2].config == {"new": True}


def test_deep_merge_replaces_lists() -> None:
    base = [PluginRow(id="a", use="m:A", config={"items": [1, 2, 3]})]
    patch = [PluginRow(id="a", use="m:A", config={"items": [9]})]
    assert deep_merge(base, patch)[0].config["items"] == [9]


# -- resolve_plugin -----------------------------------------------------------------


def test_resolve_plugin_returns_attribute() -> None:
    target = resolve_plugin("harness.test_loader:AlphaPlugin", row_id="a")
    # ``resolve_plugin`` instantiates the registered class; the returned
    # object should be a Plugin subclass instance, not the class itself.
    assert isinstance(target, AlphaPlugin)


def test_resolve_plugin_bad_shape_fails() -> None:
    with pytest.raises(PluginLoadError):
        resolve_plugin("no-colon-here", row_id="a")


def test_resolve_plugin_missing_attr_fails() -> None:
    with pytest.raises(PluginLoadError):
        resolve_plugin("harness.test_loader:NoSuchThing", row_id="a")


# -- full load -------------------------------------------------------------------


async def test_load_mounts_rows_in_order(tmp_path: Path) -> None:
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(_BASE_YAML, encoding="utf-8")
    ctx = Context()
    await Loader(ctx).load(config_path)
    assert MOUNT_ORDER == ["alpha", "beta"]
    svc = ctx.inject(AlphaService)
    assert svc.greeting == "hello"
    assert svc.count == 1  # schema default filled in
    await ctx.dispose()


async def test_load_applies_patch_layer(tmp_path: Path) -> None:
    base = tmp_path / "cordis.yml"
    base.write_text(_BASE_YAML, encoding="utf-8")
    patch = tmp_path / "patch.yml"
    patch.write_text(
        "plugins:\n"
        "  - id: alpha\n"
        "    use: harness.test_loader:AlphaPlugin\n"
        "    config:\n"
        "      greeting: patched\n",
        encoding="utf-8",
    )
    ctx = Context()
    await Loader(ctx).load(base, patch_paths=(patch,))
    assert ctx.inject(AlphaService).greeting == "patched"
    await ctx.dispose()


async def test_load_applies_env_override_with_schema_coercion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(_BASE_YAML, encoding="utf-8")
    monkeypatch.setenv("OMNISCRIBE_PLUGIN_ALPHA__COUNT", "7")
    ctx = Context()
    await Loader(ctx).load(config_path)
    svc = ctx.inject(AlphaService)
    assert svc.count == 7  # env string coerced to int by the Schema
    await ctx.dispose()


async def test_load_invalid_schema_fails_loud(tmp_path: Path) -> None:
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(
        "plugins:\n"
        "  - id: alpha\n"
        "    use: harness.test_loader:AlphaPlugin\n"
        "    config:\n"
        "      count: not-a-number\n",
        encoding="utf-8",
    )
    ctx = Context()
    with pytest.raises(PluginLoadError) as excinfo:
        await Loader(ctx).load(config_path)
    assert excinfo.value.row_id == "alpha"
    assert "invalid config" in excinfo.value.reason
    await ctx.dispose()


async def test_load_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(PluginLoadError):
        await Loader(Context()).load(tmp_path / "nope.yml")


async def test_load_plugin_raising_wraps_error(tmp_path: Path) -> None:
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(
        "plugins:\n  - id: broken\n    use: harness.test_loader:BrokenPlugin\n",
        encoding="utf-8",
    )
    ctx = Context()
    with pytest.raises(PluginLoadError) as excinfo:
        await Loader(ctx).load(config_path)
    assert "explode" in excinfo.value.reason
    await ctx.dispose()


@register_plugin
class BrokenPlugin(Plugin):
    async def apply(self, ctx: Context) -> None:
        raise RuntimeError("explode")


@register_plugin
class FailingInitPlugin(Plugin):
    def __init__(self) -> None:
        raise ValueError("initialization blew up")


async def test_load_explicit_missing_patch_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(_BASE_YAML, encoding="utf-8")
    missing_patch = tmp_path / "missing_patch.yml"
    ctx = Context()
    with caplog.at_level(logging.WARNING, logger="omniscribe.harness"):
        await Loader(ctx).load(config_path, patch_paths=(missing_patch,))
    assert any(
        "cordis patch file specified but not found" in rec.message.lower()
        and str(missing_patch) in rec.message
        for rec in caplog.records
    )
    await ctx.dispose()


async def test_load_explicit_env_missing_patch_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_patch = tmp_path / "missing_from_env.yml"
    monkeypatch.setenv("OMNISCRIBE_CORDIS_PATCH", str(missing_patch))
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(_BASE_YAML, encoding="utf-8")
    ctx = Context()
    with caplog.at_level(logging.WARNING, logger="omniscribe.harness"):
        await Loader(ctx).load(config_path, patch_paths=(missing_patch,))
    assert any(
        "cordis patch file specified but not found" in rec.message.lower()
        and str(missing_patch) in rec.message
        for rec in caplog.records
    )
    await ctx.dispose()


async def test_load_default_missing_cordis_patch_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OMNISCRIBE_CORDIS_PATCH", raising=False)
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(_BASE_YAML, encoding="utf-8")
    default_patch = tmp_path / "cordis.patch.yml"
    ctx = Context()
    with caplog.at_level(logging.WARNING, logger="omniscribe.harness"):
        await Loader(ctx).load(config_path, patch_paths=(default_patch,))
    assert not any(
        "cordis patch file specified but not found" in rec.message.lower()
        for rec in caplog.records
    )
    await ctx.dispose()


async def test_load_mounted_plugins_logs_plugin_count(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(_BASE_YAML, encoding="utf-8")
    ctx = Context()
    with caplog.at_level(logging.INFO, logger="omniscribe.harness"):
        await Loader(ctx).load(config_path)
    assert any(
        "harness mounted plugins: alpha, beta (2 plugins)" in rec.message
        for rec in caplog.records
    )
    await ctx.dispose()


async def test_instantiate_error_includes_use_and_id(tmp_path: Path) -> None:
    config_path = tmp_path / "cordis.yml"
    config_path.write_text(
        "plugins:\n  - id: bad_plugin_row\n    use: harness.test_loader:FailingInitPlugin\n",
        encoding="utf-8",
    )
    ctx = Context()
    with pytest.raises(PluginLoadError) as excinfo:
        await Loader(ctx).load(config_path)
    assert excinfo.value.row_id == "bad_plugin_row"
    assert "bad_plugin_row" in excinfo.value.reason
    assert "harness.test_loader:FailingInitPlugin" in excinfo.value.reason
    assert "cannot instantiate plugin" in excinfo.value.reason
    await ctx.dispose()

