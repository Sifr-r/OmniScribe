"""Durable default location for state and artifacts.

Assessment finding 8: ``artifact_base_dir`` defaulted to
``tempfile.gettempdir()``, so job history, result handles and the SQLite
state database were removed by ordinary system temp cleanup while the UI
still listed them.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from omniscribe.config import _default_artifact_base_dir


def test_default_artifact_base_is_not_the_system_temp_dir() -> None:
    default = _default_artifact_base_dir()
    temp_root = Path(tempfile.gettempdir()).resolve()
    assert default.resolve() != temp_root, (
        "state and artifacts must not default to the system temp directory"
    )


def test_default_artifact_base_is_per_user() -> None:
    default = _default_artifact_base_dir()
    assert default.is_absolute()
    if sys.platform == "win32":
        # %LOCALAPPDATA%\OmniScribe
        assert default.name == "OmniScribe"
        assert "OmniScribe" in default.parts
    elif sys.platform == "darwin":
        assert default.parts[-3:] == ("Library", "Application Support", "OmniScribe")
    else:
        assert default.name == "omniscribe"


def test_env_override_still_wins(monkeypatch) -> None:
    """Containers and ephemeral deployments keep the env override."""
    from omniscribe.config import load_settings

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("OMNISCRIBE_ARTIFACT_DIR", tmp)
        settings = load_settings()
        assert Path(settings.artifact_base_dir) == Path(tmp)


def test_settings_default_matches_helper(monkeypatch) -> None:
    """The field's default_factory is the documented helper."""
    from omniscribe.config import load_settings

    monkeypatch.delenv("OMNISCRIBE_ARTIFACT_DIR", raising=False)
    settings = load_settings()
    assert Path(settings.artifact_base_dir) == _default_artifact_base_dir()
