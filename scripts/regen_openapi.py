"""Regenerate tests/openapi.json from the booted FastAPI app.

Routes are added during lifespan startup, so the static ``app.openapi()``
returns a stub (one path). Boot via ``TestClient`` (which runs the
lifespan) and capture the populated schema, mirroring what
``tests/routers/test_openapi_schema.py`` checks against.

The conftest's ``_TEST_CORDIS_YML`` is the contract the snapshot is
checked against (``api_client`` boots from it), so we feed the same
YAML through here to avoid drift from the ``resources/cordis.yml``
defaults.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402


def regen(snapshot_path: Path) -> tuple[int, int]:
    """Boot the app under the conftest's contract YAML and write the
    resulting OpenAPI schema to ``snapshot_path``. Returns
    ``(byte_count, path_count)``."""
    from tests.conftest import _TEST_CORDIS_YML  # noqa: E402,F401  (intentional late import)

    with tempfile.TemporaryDirectory() as artifact_dir:
        cordis_path = Path(artifact_dir) / "cordis.yml"
        cordis_path.write_text(_TEST_CORDIS_YML, encoding="utf-8")

        os.environ["OMNISCRIBE_ARTIFACT_DIR"] = artifact_dir
        os.environ["OMNISCRIBE_CORDIS_CONFIG"] = str(cordis_path)
        # Drop env vars that would force a non-memory backend.
        for name in (
            "OMNISCRIBE_STATE_BACKEND",
            "OMNISCRIBE_STATE_DB_PATH",
            "OMNISCRIBE_CORDIS_PATCH",
        ):
            os.environ.pop(name, None)

        from omniscribe.server import create_app  # noqa: E402

        with TestClient(create_app()) as client:
            schema = client.app.openapi()

    snapshot_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return snapshot_path.stat().st_size, len(schema.get("paths", {}))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate tests/openapi.json from the booted FastAPI app."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tests" / "openapi.json",
        help="Destination path for the snapshot JSON (default: tests/openapi.json).",
    )
    args = parser.parse_args()
    byte_count, path_count = regen(args.output)
    print(f"wrote {args.output} ({byte_count} bytes, {path_count} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
