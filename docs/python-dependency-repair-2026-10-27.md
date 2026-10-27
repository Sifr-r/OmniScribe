# Python dependency repair (2026-10-27)

## Scope and blueprint

Repair the existing local Python environment from the committed dependency
lock. The dependency audit found a consistent 194-package lockfile and a
CPython 3.13.9 environment missing 29 packages required by optional lexicon,
glossary, transcription, translation-scoring, and tokenization features.
No production module or dependency-version change is planned.

| Path | Single responsibility | Planned action |
| --- | --- | --- |
| `pyproject.toml` | Declare runtime dependencies, extras, and development tools | Preserve the existing manifest |
| `uv.lock` | Record the resolved dependency versions and package hashes | Preserve the existing 194-package lock |
| `.venv/` | Hold the ignored local Python runtime and installed dependencies | Synchronize every declared extra from the lock |
| `docs/python-dependency-repair-2026-10-27.md` | Record this dependency-repair plan and its verification evidence | Add this document |
| `ARCHITECTURE.md` | Link recent workspace architecture and maintenance records | Add the final repair reference |
| `docs/ARCHITECTURE.md` | Maintain the canonical architecture ledger and file responsibilities | Record the final repair outcome and this document's responsibility |

The repair command is idempotent and keeps package selection locked:

```powershell
uv sync --locked --all-extras --cache-dir .uv-cache-codex
```

The explicit workspace cache avoids writes to the unavailable sandbox cache.
The initial package download failed because the sandbox could not resolve
`files.pythonhosted.org`; the retry uses approved network access.

## Pillow compatibility exception

Surya 0.22.1 [declares `pillow>=10.2.0,<11`](https://github.com/datalab-to/surya/blob/master/pyproject.toml), while this project deliberately
overrides Pillow to `>=12.3.0` for native AVIF support and its existing security
baseline. A plain dependency-metadata check therefore reports this conflict.
Keep the existing override: lowering Pillow would regress the project's image
support and baseline. This exception is separate from the missing optional
packages repaired here.

The pre-install import smoke passed for Surya detection and recognition with
Pillow AVIF support enabled, `torch 2.14.0+cpu`, and `torchvision 0.29.0+cpu`.
This confirms imports only; it does not establish real model inference.

## Verification record

- Passed before repair: `uv lock --check --offline --cache-dir .uv-cache-codex`
  resolved the existing 194 packages without manifest drift.
- Passed before repair: the Surya/Pillow/Torch import smoke described above.
- Passed: locked all-extras synchronization installed the 29 missing packages.
- Passed: a repeat offline all-extras dry run resolved 194 packages, checked
  168 installed packages, and would make no changes.
- Passed after repair: the lock check remains consistent. `uv pip check`
  reports only the known Surya/Pillow metadata exception described above.
- Passed after repair: imports for `lancedb`, `pyarrow`, `pandas`,
  `sentence_transformers`, `faster_whisper`, `sacrebleu`, `openpyxl`,
  `sqlalchemy`, `git`, `chardet`, `sentencepiece`, `langgraph`, `cv2`,
  `fastapi`, `uvicorn`, and `websockets`.
- The first sandbox import smoke stalled during LanceDB initialization and
  was interrupted. The smoke passed under the approved normal runtime.
- Passed: focused repository hygiene, Arrow Substrait, lexicon projection,
  query terms, real LanceDB store, and SQL/git glossary tests: **106 passed,
  1 skipped** in 10.26 seconds. The skip covers the SQLAlchemy-missing branch
  because SQLAlchemy is installed. One existing LanceDB index API deprecation
  warning remains. The sandbox run stalled during LanceDB initialization;
  the approved normal-runtime rerun passed using workspace test artifacts.

Focused test selection (run with `.venv/Scripts/python.exe -m pytest`):

```text
tests/scripts/test_repo_hygiene.py
tests/scripts/test_arrow_substrait_present.py
tests/core/lexicon/test_search_projection.py
tests/core/lexicon/test_query_terms.py
tests/core/lexicon/test_lexicon_store.py
tests/core/glossary_sources/test_glossary_sources_sql_git.py
```

Existing user edits are preserved. No application code, model weights, runtime
ownership, or wire contracts are changed by this environment repair.
