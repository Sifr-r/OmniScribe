# Multi-stage Dockerfile for the OmniScribe web app.
#
# Two stages:
#   1. ``builder`` installs ``uv`` and syncs the pinned dependency set
#      + the project itself into ``/app/.venv``.
#   2. ``runtime`` copies ``/app/.venv`` from the builder; the final
#      image has no uv toolchain, no build tools, and no build cache.
#
# Defense-in-depth: the runtime image runs as a non-root ``app`` user
# (uid 1001) and binds the default web port 8000.
#
# Extras baked in: ``web`` (FastAPI / uvicorn) + ``async-translation``
# (Celery + Redis + LangGraph) so the same image is usable for both the
# API service and the worker. Drop ``--extra async-translation`` from
# the ``uv sync`` line if you only need the synchronous HTTP surface.
#
# For GPU / CUDA support: replace the base with
# ``nvidia/cuda:...runtime-cudnn*`` and install ``torch`` matching the
# CUDA major version. Out of scope for this template.

# Pinned: 2026-08-16 to a verified Docker Hub OCI image-index digest for
# library/python:3.14-slim. Lookup performed against
# registry-1.docker.io/v2/library/python/manifests/3.14-slim
# (Content-Type: application/vnd.oci.image.index.v1+json,
#  self-digest re-lookup consistent). Satisfies the digest-pinning
# requirement in SECURITY.md (M7).
# ---- builder stage ----
FROM python:3.14-slim@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# ``uv`` from the official standalone binary keeps the image smaller
# than pip-installing it. ``UV_VERSION`` pins the installer payload
# (audit P1-7): without it the build fetches whatever ``latest`` is,
# a moving supply-chain target. Bump deliberately, in lockstep with
# the developer toolchain.
#
# Sprint 5 / H-1 audit fix: download the installer to disk and run it
# from disk so the build fails loud on a partial download (vs.
# ``curl | sh`` which would silently pipe a half-fetched payload to
# ``sh``). ``test -s`` guards against an empty file; a non-2xx would
# also fail because the installer script exits 1.
ARG UV_VERSION=0.11.16
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" -o /tmp/uv.tar.gz \
 && test -s /tmp/uv.tar.gz \
 && tar -xzf /tmp/uv.tar.gz -C /tmp \
 && install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/uv \
 && install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/uvx \
 && rm -rf /tmp/uv.tar.gz /tmp/uv-x86_64-unknown-linux-gnu \
 && rm -rf /root/.cache

WORKDIR /app

# Copy dependency manifest first so the install layer is cacheable
# independent of the source tree. ``--locked`` (audit P1-7) installs
# exactly the committed uv.lock set instead of re-resolving.
# ``LICENSE`` and ``README.md`` are required by hatchling during the
# project install (see ``pyproject.toml``: ``license = { file = "LICENSE" }``
# and ``readme = "README.md"``).
COPY pyproject.toml uv.lock LICENSE README.md ./
RUN mkdir -p /app/src/omniscribe \
 && touch /app/src/omniscribe/__init__.py \
 && uv sync --locked --extra web --extra async-translation --extra preprocessing --extra lexicon --no-install-project

# Copy the project source and complete the install.
COPY src ./src
RUN uv sync --locked --extra web --extra async-translation --extra preprocessing --extra lexicon \
 && rm -rf /root/.cache

# ---- runtime stage ----
FROM python:3.14-slim@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9 AS runtime

# Drop root for runtime. The official Python slim image ships a
# ``nonroot`` user, but we create our own so the path is stable.
# ``--system`` mirrors the pre-P1-7 user (uid 1001, no interactive
# shell — the CMD is the long-running web server, not a login).
RUN groupadd --system app && useradd --system --gid app --uid 1001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Sprint 5 / H-2 audit fix: install tini as PID 1 so SIGTERM reaches
# the Python process group. Without tini, uvicorn is PID 1 inside
# the container and its default SIGTERM handler exits abruptly
# without draining WebSocket clients / running the FastAPI lifespan
# shutdown. tini is ~30 KB and well-trusted; the official Debian
# package is the simplest source. Must run as root before dropping privileges.
#
# H-7 audit fix: drop the system ``pip`` (and its vendored deps like
# ``msgpack`` 1.1.2 + ``setuptools`` 70.3.0) that ships in
# ``python:3.14-slim``. The vendored copies aren't importable by user
# code, but trivy's pkg scanner reads the embedded ``bom.cdx.json`` and
# reports them as installed -- producing HIGH/CRITICAL false positives
# (GHSA-6v7p-g79w-8964, CVE-2025-47273) that have nothing to do with
# the project's actual dependency set. The venv at ``/app/.venv`` is
# already on PATH first and has its own ``pip`` if anything inside the
# image needs one, so the system one is dead weight.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/python3.14/site-packages/pip \
          /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
          /usr/local/lib/python3.14/site-packages/pip3 \
          /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14

# Copy the venv and source from the builder with non-root ownership.
# D5-02 audit fix: using --chown=app:app directly avoids a redundant
# RUN chown -R layer that duplicates the ~1.5GB venv in Docker storage.
COPY --chown=app:app --from=builder /app/.venv /app/.venv
# H-7b audit fix: also drop the venv's own ``pip`` -- it ships with a
# vendored copy of msgpack 1.1.2 + setuptools 70.3.0 declared in its
# ``bom.cdx.json``, which trivy reads as "installed". The runtime
# never calls ``pip`` (the project is installed via ``uv sync`` at
# build time and ``omniscribe-server`` runs at runtime), so removing
# it is safe. We also drop ``pip/_vendor/*`` outright: the BOM
# removal alone would silence the scanner but leave ~5 MB of dead
# vendored code in the image; clearing both is the minimal clean fix.
RUN rm -rf /app/.venv/lib/python3.14/site-packages/pip \
         /app/.venv/lib/python3.14/site-packages/pip-*.dist-info \
         /app/.venv/lib/python3.14/site-packages/pip3 \
         /app/.venv/bin/pip /app/.venv/bin/pip3 /app/.venv/bin/pip3.14
COPY --chown=app:app --from=builder /app/src ./src
COPY --chown=app:app --from=builder /app/pyproject.toml /app/uv.lock ./

RUN mkdir -p /app/data && chown -R app:app /app/data

ENV PATH="/app/.venv/bin:$PATH" \
    HF_HOME=/app/data/hf

USER app

EXPOSE 8000

# F5-03 / F5-04 audit fix: ``HEALTHCHECK`` is set at the Dockerfile
# level (not just in ``compose.yaml``) so non-Compose orchestrators
# (Kubernetes liveness probes, plain Docker ``--health-cmd``,
# Nomad, ECS task definitions) can detect a half-broken process.
# The probe hits ``/api/health`` — the cheap no-I/O endpoint in
# ``src/omniscribe/plugins/health.py`` — and uses Python's
# stdlib ``urllib`` so no extra apt packages are needed (matches
# the same probe the ``compose.yaml`` ``api`` service uses).
# ``--start-period`` is generous (30s) because the first request
# to a cold container pays the model-load + import-graph setup
# cost on the synchronous OCR path.
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

# Default: bind on loopback only (audit S11). The container is
# reachable from the host via Docker's port mapping
# (``-p 127.0.0.1:8000:8000``) and from other containers on the same
# Docker network via the container's IP. Operators who explicitly
# need LAN exposure should run with ``--network host`` and override
# the CMD (``docker run --network host omniscribe --host 0.0.0.0``)
# — the default of 0.0.0.0 was a footgun for unauthenticated
# deployments and is gone as of v0.2.0. ``tini`` forwards SIGTERM to
# the web server for a clean shutdown.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["omniscribe-server", "--host", "127.0.0.1", "--port", "8000"]
