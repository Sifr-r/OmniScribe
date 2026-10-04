"""Catch exclusions that break the server/Surya/Transformers import chain."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_bundle_exclusions_preserve_runtime_imports() -> None:
    """Make every exclusion unavailable in a fresh interpreter, then boot imports."""
    scratch = ROOT / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bundle-imports-", dir=scratch) as staging:
        env = os.environ.copy()
        env.update(
            OMNISCRIBE_ARTIFACT_DIR=staging,
            OMNISCRIBE_STATE_DB_PATH=str(Path(staging) / "state.db"),
            OMNISCRIBE_SPOOL_DIR=staging,
            OMNISCRIBE_STATE_BACKEND="memory",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import ast, pathlib, sys; "
                "tree = ast.parse(pathlib.Path('omniscribe_server.spec').read_text()); "
                "excludes = ast.literal_eval(next(n.value for n in tree.body "
                "if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) "
                "and t.id == 'EXCLUDES' for t in n.targets))); "
                "assert all('*' not in name for name in excludes), "
                "'PyInstaller exclusions must be explicit module names'; "
                "sys.modules.update(dict.fromkeys(excludes)); "
                "import omniscribe.server; import surya.detection; "
                "import transformers.processing_utils; import transformers.modeling_utils",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
