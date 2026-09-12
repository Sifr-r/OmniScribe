from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parents[2]
DEV_SCRIPT = ROOT / "scripts" / "dev.py"
# F5-27 audit fix: ``test-slow`` and ``security`` are now first-class
# Makefile targets (Surya + Semgrep respectively). The set is exact
# equality with what the Makefile documents, so any new target must
# land here as well — the test is the contract that the developer
# command surface and the test awareness stay in lockstep.
REQUIRED_TARGETS = {
    "help",
    "setup",
    "build-client",
    "bundle",
    "bundle-smoke",
    "run",
    "test",
    "test-slow",
    "check",
    "lint",
    "typecheck",
    "audit",
    "security",
    "clean",
    "doctor",
    "openapi",
    "pre-build",
}


def _load_dev_script() -> ModuleType:
    assert DEV_SCRIPT.exists(), "scripts/dev.py must provide clean and doctor commands"
    spec = importlib.util.spec_from_file_location("omniscribe_dev", DEV_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_makefile_documents_every_command() -> None:
    makefile = ROOT / "Makefile"
    assert makefile.exists(), "Makefile must provide the command surface"

    makefile_text = makefile.read_text(encoding="utf-8")
    documented = {
        match.group(1)
        for match in re.finditer(
            r"^([a-z][a-z-]*):[^\n]*##\s+\S.+$", makefile_text, re.MULTILINE
        )
    }
    assert documented == REQUIRED_TARGETS

    help_recipe = makefile_text.split("help:", 1)[1].split("\n\nsetup:", 1)[0]
    assert all(target in help_recipe for target in REQUIRED_TARGETS)


def test_clean_removes_only_generated_files(tmp_path: Path) -> None:
    generated = [
        tmp_path / ".pytest_cache",
        tmp_path / "src" / "pkg" / "__pycache__",
        tmp_path / "dist",
    ]
    for directory in generated:
        directory.mkdir(parents=True)
        (directory / "generated.txt").write_text("generated", encoding="utf-8")
    keep = tmp_path / "src" / "pkg" / "keep.py"
    keep.write_text("keep", encoding="utf-8")

    module = _load_dev_script()
    assert module.clean(tmp_path) == len(generated)
    assert all(not path.exists() for path in generated)
    assert keep.exists()


def test_doctor_reports_required_runtime_health(monkeypatch, capsys) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("LLM_API_BASE", "http://127.0.0.1:1/v1")

    module = _load_dev_script()
    assert module.doctor() == 0

    output = capsys.readouterr().out
    assert "Runtime health" in output
    assert "uv:" in output
    assert "Python:" in output
    assert "Redis:" in output
    assert "Model server:" in output
