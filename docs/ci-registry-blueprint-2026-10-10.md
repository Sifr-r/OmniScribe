# CI registry reliability repair

Observed infrastructure symptom: anonymous Docker Hub pulls fail with 429s or
stall. Inspect the failed run logs before attributing each stage to this cause.
Current workflows have no explicit scanner/build deadlines.

| Owner | Files | Single responsibility and acceptance |
| --- | --- | --- |
| Security agent | `.github/workflows/security.yml` | Remove Semgrep job-container Docker Hub pull; install the same pinned OSS CLI using a verified alternative; bound installation/scan/job durations and preserve rules, finding failures and SARIF. |
| Container agent | `Dockerfile`, container-scan section of `.github/workflows/test.yml` | Verify digest-preserving public mirror for Python base and trusted non-Hub BuildKit distribution; make Trivy image source and database registries explicit; bound setup/build/scan/job; preserve scan enforcement. |
| Orchestrator | This document, `ARCHITECTURE.md`, `docs/ARCHITECTURE.md` | Review evidence and changes, record validation and remaining external limitations. |

No new production modules. This document is the only planned new tracked file;
it owns the repair blueprint and observed validation evidence. Prefer existing
workflow mechanisms, no secrets or registry accounts, no swallowed build/scan failures or
unbounded retries. Validate inputs, contextual errors, typing and idempotency
where introduced. All writes stay within this project. Git operations remain
limited to log, status and diff; no local push or live-run cancellation.

## Confirmed evidence

- Security run 37992123253: Docker Hub reports `toomanyrequests` twice for
  `semgrep/semgrep:1.173.0`, followed by registry HTTP 500. Job initialization
  fails before checkout, so a scanner-step retry cannot repair this path.
- Tests run 37992123329: the pinned Python manifest HEAD returns HTTP 429 from
  `registry-1.docker.io`, aborting the image build before Trivy runs.
- Tests run 38049299823: the build runs roughly ten minutes before cancellation.
  That record alone does not establish the cause of the reported 80-minute hang;
  continue the timing audit rather than attributing every delay to rate limits.
- Tests run 37992414213 attempt 1: BuildKit bootstrap times out at
  `auth.docker.io`, then the Python manifest HEAD returns HTTP 429. Uploading
  absent SARIF creates a secondary failure; only upload a generated report.
- Successful run 37989132849: image build takes 32m33s, including 27m06.6s
  exporting GitHub Actions cache. Bound cache export independently from build
  and scanner deadlines; rate-limit fixes alone do not address this delay.
  Cache export is an optional optimization: allow logged export warnings after
  its five-minute deadline without bypassing image-build or scanner failures.

## Final implementation and verification

- `security.yml`: no job container. Install exactly Semgrep 1.173.0 from PyPI
  in a runner-temp venv; retain all four registry rule configs, `--error`,
  metrics policy and SARIF. Install deadline 5m, CLI deadline 8m with forced
  termination after 30s, scan step 10m, upload 3m, job 20m.
- Both Docker stages use `public.ecr.aws/docker/library/python` with the
  unchanged `sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9`
  index. Mirror availability and exact digest verified live.
- Container CI installs pinned Docker 29.7.2 from the official binary archive
  using SHA-pinned setup-docker v5.5.0, enables containerd image storage and
  selects Buildx's `docker` driver. No `moby/buildkit` image pull is required;
  the image store preserves native-driver support for GHA caching.
- Container deadlines: daemon setup 5m, Buildx setup 3m, build 35m, scanner
  step 8m/CLI 5m, upload 3m, entire job 50m. GHA cache import/export each has
  a 5m timeout. Export-only `ignore-error=true` allows logged cache-upload
  failures; build/scanner failures remain fatal.
- Trivy uses only the local Docker daemon image, explicit GHCR database and
  Java database sources, and the existing HIGH/CRITICAL SARIF gate and scoped
  advisory exception. SARIF uploads require the corresponding file to exist.
- Linux Python 3.12 Semgrep proof: exact-version isolated PyPI installation
  and version command pass; clean fixture exits 0 with zero SARIF findings,
  matching finding fixture exits 1 with one finding. Disposable containers use
  public ECR and read-only workspace mounts; no local project environment changes.
- Minimal native-driver image build using the exact mirrored Python digest
  passes. A deliberately unavailable optional cache exporter logs its error
  while the successful build exits 0, verifying the intended failure boundary.
- Fresh GHCR Trivy database download succeeds in 7 seconds. A missing local
  image fails immediately with the `docker` source only, proving no remote
  registry fallback. Production Dockerfile static checks pass. Evidence remains
  in ignored `reports/container-source-probe-20261010/build.log`,
  `reports/trivy-ghcr-db-20261010.log` and
  `reports/trivy-docker-only-20261010.log`. Local daemon settings were unchanged.
- Both workflows parse, whitespace checks pass, and independent review verifies
  pinned action inputs, Trivy flags, secret-free fork compatibility, identical
  base digests and unchanged scanner enforcement. No actionable review findings.

No full ML image rebuild or backend test rerun is needed for unchanged source,
lockfile and Python digest. The new hosted Docker/containerd setup and actual
registry-rule/network performance still require GitHub runner execution.
On 2026-10-11 the user authorized publishing these tested fixes through GitHub
APIs. A follow-up PR will validate the hosted setup before merge; no local Git
mutation is required. The reported 80-minute stall was not independently located.
