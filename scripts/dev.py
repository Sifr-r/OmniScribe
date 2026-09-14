from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Final
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
CACHE_DIRS: Final = (
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
    "htmlcov",
)
SOURCE_DIRS: Final = ("src", "tests", "scripts")


def clean(root: Path = PROJECT_ROOT) -> int:
    """Remove generated project artifacts without touching environments or source."""
    targets = {root / name for name in CACHE_DIRS}
    targets.update(root.glob(".coverage*"))
    targets.update((root / "src").glob("*.egg-info"))
    for source_dir in SOURCE_DIRS:
        base = root / source_dir
        if base.exists():
            targets.update(base.rglob("__pycache__"))

    removed = 0
    for target in sorted(targets, key=lambda path: len(path.parts), reverse=True):
        if target.is_dir():
            shutil.rmtree(target)
            removed += 1
        elif target.exists():
            target.unlink()
            removed += 1
    return removed


def _uv_health() -> tuple[bool, str]:
    executable = shutil.which("uv")
    if executable is None:
        return False, "not found in PATH"
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return True, result.stdout.strip()


def _python_health() -> tuple[bool, str]:
    version = ".".join(str(part) for part in sys.version_info[:3])
    return sys.version_info >= (3, 11), f"{version} (requires 3.11+)"


def _redis_health() -> tuple[bool, str]:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    parsed = urlparse(redis_url)
    host = parsed.hostname
    port = parsed.port or 6379
    if host is None:
        return False, f"invalid REDIS_URL: {redis_url}"
    try:
        with socket.create_connection((host, port), timeout=1):
            return True, f"reachable at {host}:{port}"
    except OSError as exc:
        return False, f"unavailable at {host}:{port} ({exc})"


def _model_server_health() -> tuple[bool, str]:
    api_base = (
        os.getenv("LLM_API_BASE")
        or os.getenv("OPENAI_API_BASE")
        or "http://localhost:1234/v1"
    )
    models_url = f"{api_base.rstrip('/')}/models"
    headers = {}
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        with urlopen(Request(models_url, headers=headers), timeout=2) as response:
            payload = json.load(response)
    except (OSError, URLError, ValueError) as exc:
        return False, f"unavailable at {models_url} ({exc})"

    models = payload.get("data", []) if isinstance(payload, dict) else []
    return True, f"reachable at {models_url} ({len(models)} model(s) loaded)"


def doctor() -> int:
    """Report core runtime and optional service health."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    checks = (
        ("uv", *_uv_health(), False),
        ("Python", *_python_health(), False),
        ("Redis", *_redis_health(), True),
        ("Model server", *_model_server_health(), True),
    )

    # Phase 2.4 (2026-09-05): on every failure (ERROR or WARN), print a
    # pointer to the matching ``docs/TROUBLESHOOTING.md`` anchor so a
    # stuck user has one click to the fix. Labels not in the map
    # (none today) fall through silently.
    HINTS: Final[dict[str, str]] = {
        "uv": "uv-is-not-recognized",
        "Python": "python-311-is-not-installed",
        "Redis": "make-doctor-says-redis-is-unreachable-but-i-dont-use-redis",
        "Model server": "ocr-returns-nothing",
    }

    print("Runtime health")
    print("--------------")
    core_healthy = True
    for label, healthy, detail, optional in checks:
        level = "OK" if healthy else "WARN" if optional else "ERROR"
        print(f"{level:5} {label}: {detail}")
        if not healthy:
            anchor = HINTS.get(label)
            if anchor:
                # ASCII arrow on purpose: the default Windows console
                # encoding (cp1252) cannot encode U+2192, and this is
                # the kind of detail that must work in a stuck user's
                # terminal without ceremony. The TROUBLESHOOTING.md
                # anchor itself uses proper UTF-8.
                print(f"      -> see docs/TROUBLESHOOTING.md#{anchor}")
        if not healthy and not optional:
            core_healthy = False
    return 0 if core_healthy else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="OmniScribe developer commands")
    parser.add_argument("command", choices=("clean", "doctor"))
    args = parser.parse_args()

    if args.command == "clean":
        print(f"Removed {clean()} generated path(s).")
        return 0
    return doctor()


if __name__ == "__main__":
    raise SystemExit(main())
