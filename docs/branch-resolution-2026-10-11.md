# GitHub branch resolution

User authorized merging compatible dependency PRs and deleting their merged
branches. The user subsequently authorized publication of the tested CI registry and timeout fixes.
No local Git mutation, rebase, reset or push is required.

| Owner | Scope | Acceptance |
| --- | --- | --- |
| Dependency agent | PR 73 Pillow | Read-only compatibility and lock resolution validation. |
| Container agent | PR 76 PyArrow | Read-only compatibility and conflict analysis, preserving main changes. |
| Test agent | PR 84 mypy | Read-only compatibility and typing validation. |
| Orchestrator | Remote branch updates, merge readiness, branch cleanup | Refresh branches against main, resolve only the identified manifest conflict, require checks before merge, delete only merged head branches. |

This new document owns resolution scope and evidence; architecture ledgers record
its single responsibility. Temporary verification artifacts stay inside ignored
workspace directories. No new production files. Require deterministic errors,
input validation, strict types and idempotent branch mutations using expected
commit SHAs; no unchecked force updates or changes to unrelated branches.

## Evidence and branch repairs

- PR 73: current main already locks Pillow 12.3.0. Thirty focused imaging
  tests pass. Updated branch from main, preserved both requested requirement
  floors, and corrected exactly two lock metadata entries; lock check passes.
- PR 76: conflict came from adjacent LanceDB, pandas and sentence-transformers
  requirement changes. Preserve all main changes and change only the two
  PyArrow floors and two lock metadata entries. PyArrow remains 25.0.1.
  Isolated Arrow schema, timestamps, fixed vector width, filtering, vector
  search, idempotent table creation and embedding mismatch checks pass.
- PR 84: mypy 2.4.0 checks 446 source/test files successfully. Lock updates
  include mypy and required ast-serialize/librt versions only; all unrelated
  package records are preserved. Lock check passes. The prior hardcoded-path
  test failure is corrected on main; its focused test passes.
- Applied GitHub commits with expected-head checks and fast-forward updates,
  preserving history and main's changes. Remote diff confirms only
  `pyproject.toml` and `uv.lock` are changed. Original LF line endings retained.
- All three dependency PRs passed fresh hosted checks and are merged: mypy #84,
  PyArrow #76 and Pillow #73. Their remote head branches are deleted.
  Pillow's final merge preserves the independently tested combined manifest
  and lockfile after resolving overlap with the other two merges.
- The approved CI reliability changes are published in a follow-up PR against
  the merged dependency state. Hosted checks must pass before its merge and
  branch deletion. The local checkout and active environment remain untouched.
- Combined Pillow/PyArrow/mypy candidate also passes `uv lock --check`
  (194 packages), proving the merged manifest and lockfile constraints agree.


## Final hosted verification (2026-10-11)

PR #96 https://github.com/Sifr-r/OmniScribe/pull/96 passed all nine hosted checks on 52e9b41bea50850b8e40411543eb879d7e2d7fbe. Semgrep completed in 49 seconds; all four Python jobs and Flutter passed. Docker/containerd setup and native builder passed, the image build took 23m25s, Trivy took 1m53s and SARIF upload took 8s. The PR merged as 3f345922f52cbc97ccb1db8bc2a372a4309d7745. Its branch is deleted; GitHub has only main and no open PRs.
