"""Repo-hygiene sanity tests for the DX/infra files added in Phase 5.

Each test pins a small but meaningful invariant about the
infrastructure files so a drive-by refactor doesn't silently
disconnect them. The expected shape is documented in
``compose.yaml`` / ``Dockerfile`` / ``.pre-commit-config.yaml`` /
``.github/workflows/nightly.yml``.

A second cluster of tests pins architecture-boundary invariants from
``ARCHITECTURE.md`` so a refactor doesn't silently collapse the seams
that the recent god-module decompositions established (see the
``core.workflows`` engine split and the OCR router / state module
decomposition entries).
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports_from(path: Path, *module_prefixes: str) -> list[tuple[str, int]]:
    """Return ``[(full_module, lineno)]`` for every import matching a prefix.

    Uses :mod:`ast` so docstrings, comments, and dunder strings don't
    produce false positives — only actual ``import`` and ``from … import``
    statements are considered. A match is any module whose dotted path
    equals one of ``module_prefixes`` or starts with one of them
    followed by ``.``.
    """
    tree = ast.parse(_read(path))
    matches: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if any(
                    full == prefix or full.startswith(prefix + ".")
                    for prefix in module_prefixes
                ):
                    matches.append((full, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(
                    alias.name == prefix or alias.name.startswith(prefix + ".")
                    for prefix in module_prefixes
                ):
                    matches.append((alias.name, node.lineno))
    return matches


#: Engine-implementation submodules of ``omniscribe.core.workflows``. Callers
#: outside the package itself must reach the engines via
#: ``omniscribe.OCRPipeline`` (or the public ``omniscribe.core.workflows``
#: re-exports). The ``base`` module is intentionally excluded — it exposes the
#: public ``DocumentResultWriter`` protocol and callback type aliases that
#: peers (e.g. ``core/pdf/__init__.py``) reasonably need.
_WORKFLOW_INTERNAL_SUBMODULES: tuple[str, ...] = ("hybrid", "grounded", "utils")


def test_dockerfile_pins_python_and_uv_installs_extras():
    dockerfile = _read(ROOT / "Dockerfile")

    # Pinned Python - keeps the build deterministic.
    # Updated 2026-08-16: dependabot PR #22 bumped the base from 3.12-slim
    # to 3.14-slim. Pin the regex to whatever the Dockerfile currently has.
    m = re.search(r"^FROM python:(\d+\.\d+)-slim@", dockerfile, re.MULTILINE)
    assert m, (
        "Dockerfile must pin a specific Python base image (e.g. FROM python:3.14-slim@...)"
    )
    assert m.group(1) in {"3.12", "3.13", "3.14"}, (
        f"Dockerfile pins python:{m.group(1)}-slim; expected 3.12/3.13/3.14"
    )

    # Must install web extras so the API server can boot, plus
    # preprocessing so the default-on whitespace recall pass has cv2.
    for extra in ("--extra web", "--extra async-translation", "--extra preprocessing"):
        assert extra in dockerfile, f"Dockerfile missing `{extra}` flag"

    # Uses ``omniscribe-server`` so the console script is on PATH.
    assert "omniscribe-server" in dockerfile


def test_compose_yaml_defines_exactly_api_and_redis_services():
    compose = yaml.safe_load(_read(ROOT / "compose.yaml"))

    services = compose["services"]
    assert set(services) == {"api", "redis"}, (
        "compose.yaml must define exactly the `api` and `redis` services — "
        "the Celery `worker` service was retired (async translation rides "
        "the in-process harness JobQueue)"
    )

    # No service may carry a `profiles:` key. Compose has no implicit
    # default profile, so a `profiles:` key would *exclude* the service —
    # the always-on pair must start on a plain `docker compose up`.
    # (Audit P0-4: `profiles: ["default"]` made `compose up` start nothing.)
    profiled = {name for name, svc in services.items() if svc.get("profiles")}
    assert not profiled, (
        f"no compose service may carry a `profiles:` key (found: {sorted(profiled)})"
    )


def test_precommit_config_pins_ruff_and_uvlock_hooks():
    cfg = yaml.safe_load(_read(ROOT / ".pre-commit-config.yaml"))
    repos = {repo["repo"] for repo in cfg["repos"]}
    assert "https://github.com/astral-sh/ruff-pre-commit" in repos
    assert "https://github.com/astral-sh/uv-pre-commit" in repos


def test_precommit_mypy_runs_in_the_project_environment():
    """The mypy hook must type-check with the project venv, not an
    isolated mirror env.

    ``mirrors-mypy`` installs mypy into a clean pre-commit env with no
    project dependencies, so import resolution diverges from CI's
    ``uv run mypy src`` (audit backlog). A ``language: system`` hook
    that shells out to ``uv run mypy`` keeps both gates on the same
    interpreter and dependency set.
    """
    cfg = yaml.safe_load(_read(ROOT / ".pre-commit-config.yaml"))
    mypy_hooks = [
        hook
        for repo in cfg["repos"]
        for hook in repo.get("hooks", [])
        if hook.get("id") == "mypy"
    ]
    assert mypy_hooks, "pre-commit config lost its mypy hook"
    hook = mypy_hooks[0]
    assert hook.get("language") == "system", (
        "mypy hook must run in the project environment "
        "(language: system), not an isolated mirror env"
    )
    assert "uv run mypy" in hook.get("entry", ""), (
        "mypy hook entry must delegate to `uv run mypy` so it picks "
        "up the synced project venv"
    )


def test_pyproject_has_no_duplicate_deps_across_extras():
    """A package pinned in the base deps must not be re-declared in an
    extra (audit backlog: duplicated declarations drifted across
    extras). torch / torchvision must also carry an upper major bound
    so an upstream major can't silently break surya.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_bytes().decode("utf-8"))[
        "project"
    ]

    def _name(dep: str) -> str:
        return re.split(r"[<>=!~\[;\s]", dep, maxsplit=1)[0].lower()

    base = {_name(dep) for dep in project["dependencies"]}
    # Deliberate repeats that document a feature surface and are pinned
    # by another contract test (test_optional_extras_split_chromadb_into_memory).
    allowed_overlaps = {("memory", "chromadb")}
    for extra_name, deps in project["optional-dependencies"].items():
        overlap = {_name(dep) for dep in deps} & base
        overlap -= {pkg for (ex, pkg) in allowed_overlaps if ex == extra_name}
        assert not overlap, (
            f"extra `{extra_name}` re-declares base dependencies: "
            f"{sorted(overlap)} — remove the duplicate from the extra"
        )

    by_name = {_name(dep): dep for dep in project["dependencies"]}
    for pkg in ("torch", "torchvision"):
        assert "<" in by_name[pkg], (
            f"{pkg} must carry an upper major bound ({by_name[pkg]!r})"
        )


def test_nightly_workflow_targets_slow_tests_with_hf_cache():
    workflow = _read(ROOT / ".github/workflows/nightly.yml")

    # Must explicitly opt-in to slow tests; otherwise nightly is a no-op
    # duplicate of the fast tier.
    assert re.search(r"pytest[^\n]*-m slow", workflow), (
        "nightly workflow must run `pytest -m slow`"
    )

    assert "huggingface" in workflow, (
        "nightly workflow should cache the HF Hub snapshot for Surya"
    )
    assert "schedule:" in workflow, "nightly workflow must be cron-triggered"
    assert "workflow_dispatch" in workflow, (
        "manual dispatch should be available for ad-hoc runs"
    )


def test_compose_has_no_celery_and_api_keeps_uvicorn_entrypoint():
    """The Celery worker service was retired (async translation
    dispatches on the in-process harness JobQueue), so compose.yaml
    must carry no Celery references, and the api service must keep
    booting the uvicorn-based ``omniscribe-server`` entrypoint from
    the Dockerfile CMD.

    Also verify legacy Svelte launcher scripts (start_app.vbs, stop_app.bat)
    have been removed.
    """
    compose_text = _read(ROOT / "compose.yaml")
    assert "celery" not in compose_text.lower(), (
        "compose.yaml must not reference Celery — the worker service was "
        "retired; async translation rides the harness JobQueue"
    )

    compose = yaml.safe_load(compose_text)
    assert "command" not in compose["services"]["api"], (
        "api must not override the Dockerfile CMD so the container keeps "
        "booting the uvicorn-based `omniscribe-server` entrypoint"
    )

    assert not (ROOT / "start_app.vbs").exists(), (
        "start_app.vbs has been removed in favor of the Flutter client"
    )
    assert not (ROOT / "stop_app.bat").exists(), "stop_app.bat has been removed"


def test_pyproject_extras_present_for_docker_layering():
    """The Dockerfile's ``uv sync --extra web --extra async-translation
    --extra preprocessing`` must reference real extras declared in
    ``pyproject.toml``.
    """
    extras = tomllib.loads((ROOT / "pyproject.toml").read_bytes().decode("utf-8"))[
        "project"
    ]["optional-dependencies"]
    assert "web" in extras
    assert "async-translation" in extras
    assert "preprocessing" in extras


# ---------------------------------------------------------------------------
# Architecture-boundary assertions (ARCHITECTURE.md)
# ---------------------------------------------------------------------------
#
# Pin the single-responsibility invariants that the most recent god-module
# decompositions put in place. A drive-by refactor that quietly imports a
# workflow implementation submodule, or that bypasses the state singleton,
# would collapse the seams these boundaries protect. These tests assert
# against the file tree statically (AST) so they run without booting the
# OCR pipeline or touching the VLM client.


def _is_workflow_internal(module: str) -> bool:
    """Return True iff ``module`` points at one of the engine implementation
    submodules (``hybrid`` / ``grounded`` / ``utils``) rather than the public
    re-export surface ``omniscribe.core.workflows``.
    """
    parts = module.split(".")
    return (
        len(parts) >= 4
        and parts[0] == "omniscribe"
        and parts[1] == "core"
        and parts[2] == "workflows"
        and parts[3] in _WORKFLOW_INTERNAL_SUBMODULES
    )


def test_ocr_router_does_not_import_workflow_internals():
    """``plugins/ocr/plugin.py`` is a thin orchestrator per ARCHITECTURE.md.

    It must delegate to ``OCRPipeline`` via ``plugins/ocr/pipeline_bridge``
    and never reach into ``omniscribe.core.workflows.hybrid`` /
    ``omniscribe.core.workflows.grounded`` directly. Importing either
    engine implementation would collapse the ``OCRPipeline`` facade and
    let the FastAPI router depend on internal engine layout that the
    engine-split refactor intentionally hid behind the public package
    surface.
    """
    ocr_path = ROOT / "src/omniscribe/plugins/ocr/plugin.py"
    offenders = [
        (mod, lineno)
        for mod, lineno in _imports_from(ocr_path, "omniscribe.core.workflows")
        if _is_workflow_internal(mod)
    ]
    assert not offenders, (
        "OCR plugin must not import `omniscribe.core.workflows.{hybrid,grounded,utils}` "
        "directly; go through `omniscribe.pipeline.OCRPipeline` and "
        f"`plugins/ocr/pipeline_bridge`. Found: {offenders}"
    )


def test_plugin_layer_does_not_import_workflow_internals():
    """Generalization of the OCR plugin boundary to the whole plugin tree.

    Every module under ``src/omniscribe/plugins/`` must keep the engine
    implementation details behind the ``OCRPipeline`` facade. This
    walker-driven test catches the same boundary violation in any
    plugin, not just the one highlighted by the dedicated OCR test above.
    """
    plugins_root = ROOT / "src/omniscribe/plugins"
    offenders: list[str] = []
    for path in sorted(plugins_root.rglob("*.py")):
        for mod, lineno in _imports_from(path, "omniscribe.core.workflows"):
            if _is_workflow_internal(mod):
                rel = path.relative_to(ROOT).as_posix()
                offenders.append(f"{rel}:{lineno}: {mod}")
    assert not offenders, (
        "Plugin layer must not import `omniscribe.core.workflows.{hybrid,grounded,utils}` "
        "directly; go through `omniscribe.pipeline.OCRPipeline` and "
        "`plugins/ocr/pipeline_bridge`.\n" + "\n".join(offenders)
    )


def test_state_backend_plugin_is_the_single_state_seam():
    """``plugins/state_backend.py`` is the single source of state backends.

    The harness rebuild replaced the ``api/routers/state.py`` singleton
    module with dependency injection: exactly one plugin registers the
    ``StateBackend`` service on the harness Context, and the selector
    only accepts the shipped backends. A second registration site (or a
    new unlisted backend name) would silently fork the state surface, so
    both facts are pinned here.
    """
    plugins_root = ROOT / "src/omniscribe/plugins"
    registrars: list[str] = []
    for path in sorted(plugins_root.rglob("*.py")):
        text = _read(path)
        if re.search(r"ctx\.service\(StateBackend\b", text):
            registrars.append(path.relative_to(ROOT).as_posix())
    assert registrars == ["src/omniscribe/plugins/state_backend.py"], (
        f"exactly one plugin may register the StateBackend service; found: {registrars}"
    )

    from omniscribe.plugins.state_backend import _ALLOWED_BACKENDS

    # Sprint 4 (RFC 003): the redis backend is now an accepted
    # option for Profile 4 multi-worker LAN deployments. The
    # allowlist is the lock that prevents an unsanctioned
    # backend name from silently shipping; adding a new one
    # requires a deliberate test update here.
    assert sorted(_ALLOWED_BACKENDS) == ["memory", "redis", "sqlite"]


def test_scripts_are_in_ruff_scope():
    """``scripts/**`` must stay inside the Ruff lint surface.

    The P0 XXE in ``scripts/ingest_lexicon.py`` slipped through because
    ``pyproject.toml`` excluded the whole ``scripts/`` tree from Ruff
    (P2 audit #16). Re-introducing that exclude — even by accident —
    would re-open the gap. Pin the invariant.
    """
    pyproject = _read(ROOT / "pyproject.toml")
    m = re.search(
        r"extend-exclude\s*=\s*\[[^\]]*\]", pyproject, re.MULTILINE | re.DOTALL
    )
    assert m, "pyproject.toml must have an extend-exclude list"
    assert '"scripts/**"' not in m.group(0) and "'scripts/**'" not in m.group(0), (
        "scripts/** must NOT be in extend-exclude — Ruff should lint scripts "
        "so future bugs (like the P0 XXE in ingest_lexicon.py) are caught at lint time"
    )


def test_memory_and_lexicon_extras_resolve_to_same_install_set():
    """``[memory]`` is a one-release deprecation alias of ``[lexicon]``.

    The migration spec (``docs/lexicon-migration-spec.md`` §10) renames
    the ChromaDB-backed ``memory`` extra to the LanceDB-backed ``lexicon``
    extra. To keep both spellings installable for the deprecation window,
    the two extras MUST resolve to the same install set — otherwise
    `uv sync --extra memory` and `uv sync --extra lexicon` would pull
    in different deps and the alias would silently lie.

    The check is structural: we parse ``pyproject.toml`` with
    :mod:`tomllib` and compare the sorted requirements of each extra.
    A literal-string comparison is intentional; the audit cares that
    the *extras look identical*, not that ``pip`` would deduplicate
    them after PEP 508 marker resolution.

    ``README.md`` must still mention the ``memory`` alias so the
    deprecation pointer stays discoverable in the rendered docs.
    """
    with (ROOT / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)

    optional = data["project"].get("optional-dependencies", {})
    assert "memory" in optional, (
        "pyproject.toml must keep the [memory] deprecation alias for one "
        "release; remove this assertion once the alias is dropped"
    )
    assert "lexicon" in optional, (
        "pyproject.toml must define the canonical [lexicon] extra; see "
        "docs/lexicon-migration-spec.md for the migration plan"
    )

    memory_reqs = sorted(optional["memory"])
    lexicon_reqs = sorted(optional["lexicon"])
    assert memory_reqs == lexicon_reqs, (
        f"[memory] and [lexicon] must resolve to the same install set "
        f"(deprecation alias). Got:\n  memory  = {memory_reqs}\n  "
        f"lexicon = {lexicon_reqs}"
    )

    readme = _read(ROOT / "README.md")
    assert "memory" in readme, (
        "README.md must still mention the [memory] deprecation alias "
        "so the migration pointer stays discoverable in the rendered docs"
    )
