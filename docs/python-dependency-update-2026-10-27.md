# Python dependency update (2026-10-27)

## Scope and file responsibilities

Update Python dependencies to the latest compatible resolution within the
project's existing Python support and reviewed dependency bounds. Retain the
Surya/Pillow override and add a reviewed filelock override for security fixes.
No new production module is introduced. The final dependency refresh is installed
and the validation below is complete.

| Path | Single responsibility | Planned action |
| --- | --- | --- |
| `pyproject.toml` | Declare supported dependencies, extras, and development tools | Raise the Pillow floor/override to 13.0.0 and add the reviewed `filelock>=4.2.1,<5` override |
| `uv.lock` | Record resolved versions, platform markers, and package hashes | Refresh the latest compatible dependency resolution |
| `Makefile` | Expose local dependency-audit and developer commands | Remove the obsolete LangGraph SDK vulnerability exception |
| `.github/workflows/test.yml` | Run the supported Python CI matrix and dependency audit | Remove the same obsolete vulnerability exception |
| `.venv/` | Hold the ignored installed Python runtime dependencies | Synchronize every declared extra from the updated lock |
| `.agent-tmp/python-dependency-update/lock-before.toml` | Preserve the ignored pre-update lock for rollback | Capture before updating |
| `.agent-tmp/python-dependency-update/pyproject-before.toml` | Preserve the ignored pre-update manifest for rollback | Capture before updating |
| `docs/python-dependency-update-2026-10-27.md` | Record this update's blueprint, package delta, and validation evidence | Add and finalize this record |
| `ARCHITECTURE.md` | Link recent architecture and maintenance records | Link the final outcome |
| `docs/ARCHITECTURE.md` | Maintain the canonical architecture ledger and file responsibilities | Record the final outcome and this document's responsibility |

The earlier [environment repair](python-dependency-repair-2026-10-27.md)
installed missing optional packages without changing dependency versions. This
update is a separate operation and preserves that repair record.

## Forward path and rollback

1. Capture the manifest and lock under the workspace rollback paths above.
2. Run `uv lock --upgrade --cache-dir .uv-cache-codex` within current bounds.
3. Review the resolved package delta and any compatibility constraints.
4. Run `uv sync --locked --all-extras --cache-dir .uv-cache-codex`.
5. Check imports, Ruff lint/format, the CI `mypy src tests` gate, and the fast
   test tier (`not slow and not slow_dataset`); record actual results.
6. Synchronize both architecture ledgers once verification completes.

To roll back, restore the saved manifest and lock to their original workspace
paths, then run the locked all-extras synchronization above. This restores the
prior dependency selection without destructive Git commands or data changes.
Retries use the lock and remain idempotent; a partial download or failed check
must remain visible in the final evidence.

## Resolved package changes

A live PyPI `uv lock --upgrade --dry-run` established the candidates before
the offline lock mutation. The lock now has 199 package entries instead of
194: 87 package names have updated version sets, and three names were added
(`httpcore2`, `opentelemetry-api`, and `truststore`). Platform/version marker
forks account for the difference between package-name and lock-entry counts.
The manifest raises the Pillow base requirement and override to `>=13.0.0`
and adds `filelock>=4.2.1,<5`. The Python floor remains `>=3.11`; no
prereleases are selected. Filelock was already among the 87 updated names,
so its final candidate does not increase the package-name or entry counts.

| Package | Before | Updated |
| --- | --- | --- |
| FastAPI | 0.141.1 | 0.162.0 |
| Pillow | 12.3.0 | 13.0.0 |
| Torch | 2.14.0 | 2.14.1 |
| Transformers | 5.16.1 | 5.21.0 |
| LanceDB | 0.39.0 | 0.40.0 |
| PyArrow | 25.0.1 | 26.0.0 |
| pandas | 3.0.6 | 3.1.0 |
| LangGraph SDK | 0.3.15 | 0.4.10 |
| Ruff | 0.16.6 | 0.17.3 |
| filelock | 3.32.5 | 4.2.4 |

## Compatibility and verification record

- The initial locked all-extras sync installed 87 packages, replacing 83 existing
  packages. A repeat dry run checked 172 installed packages and would make
  no changes before the final filelock override. The final sync installed
  filelock 4.2.4 and rebuilt the editable project; the final lock check resolves
  199 entries, and the repeat all-extras dry run checks 172 installed packages
  with no changes required.
- All 23 dependency-module imports pass, and Pillow reports native AVIF
  support. Preserve the deliberate Pillow override: Surya's restrictive
  Pillow metadata still produces the documented warning with Pillow 13.
  The [Pillow 13 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html)
  document the security fixes that motivate the higher base requirement.
- Ruff 0.17.3 lint and format checks pass for 447 files; the CI
  `mypy src tests` gate passes for 447 files.
- The full fast tier on the initial updated environment (filelock 3.32.7)
  passed: **2,913 passed, 2 skipped, 13 deselected**, 19 warnings, in 288
  seconds, with **84.21% coverage**. The skips cover the SQLAlchemy-missing
  branch because SQLAlchemy is installed and the absent optional pytesseract.
- LangGraph SDK 0.4.10 resolves with WebSockets 17.3. The former SDK audit
  exception was removed from both the local and CI audit commands.
  The [upstream advisory](https://github.com/langchain-ai/langgraph/security/advisories/GHSA-fvww-7h3r-vfhp)
  identifies 0.4.4 as the fixed version.
- The first audit found three vulnerabilities in the intermediate filelock
  3.32.7 resolution. The advisories
  [GHSA-v4x8-3wr5-3wq8](https://github.com/tox-dev/filelock/security/advisories/GHSA-v4x8-3wr5-3wq8),
  [GHSA-9vhv-jxww-xfm5](https://github.com/advisories/GHSA-9vhv-jxww-xfm5), and
  [GHSA-567q-5qvw-pf92](https://github.com/tox-dev/filelock/security/advisories/GHSA-567q-5qvw-pf92)
  identify 4.2.1 as fixed. The final lock selects 4.2.4 through the explicit
  override of Surya's `<4` cap.
- Source review found the consumers use public APIs: Surya's
  `FileLock(timeout=120)` context manager, Hugging Face's `WeakFileLock`, and
  Torch's public `FileLock` subclass. Filelock 4's changed `SoftReadWriteLock`
  protocol is unused by these paths. After installing 4.2.4, real smoke checks
  passed for `FileLock`, `SoftFileLock`, Hugging Face `WeakFileLock`, and Torch
  `FileLock`, including repeated acquire/release and timeout of a competing
  acquisition. These checks used no model downloads or network calls.
- The final `pip-audit --skip-editable`, without vulnerability-ignore flags,
  audits 172 dependency rows and reports **no known vulnerabilities** (exit 0).
  Only the editable OmniScribe project itself is skipped. The final JSON evidence is
  `.agent-tmp/python-dependency-update/pip-audit-final.json`; the initial
  failed audit remains at `pip-audit.json` in the same directory.
- `uv pip check` reports exactly two expected Surya metadata conflicts:
  Pillow 13.0.0 and filelock 4.2.4 exceed Surya's declared upper bounds.
  Both are explicit, reviewed `uv` overrides; the warnings are not suppressed.
- Independent static review found no manifest/lock or dev-requirement drift,
  no prerelease packages, matching `memory`/`lexicon` aliases, and the paired
  Torch 2.14.1/Torchvision 0.29.1 versions. Local and CI audits are ignore-free.
- The final filelock 4.2.4 environment passed the focused aligner, OCR, imaging,
  and image tests: **237 passed, 1 expected pytesseract skip** in 23.69 seconds
  (exit 0). Selection: `tests/core/test_aligner.py tests/core/ocr
  tests/core/imaging tests/utils/test_image.py`, excluding `slow` and
  `slow_dataset`. JUnit evidence is
  `.agent-tmp/python-dependency-update/junit-final-filelock.xml`.
- Surya detection and recognition imports pass after the final filelock sync
  with Torch 2.14.1+cpu and Torchvision 0.29.1+cpu.
- Both architecture ledgers now record this update and this document's single
  responsibility.

Local import/static-check success does not establish model inference,
packaging, hosted CI, or cross-platform compatibility.
