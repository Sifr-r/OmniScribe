# Contributing to OmniScribe

Thanks for your interest in OmniScribe. This document is the short
version; the long version is
[`docs/AGENTS.md`](docs/AGENTS.md), which every contributor is
expected to have read.

## TL;DR

1. **Read [`docs/AGENTS.md`](docs/AGENTS.md).** It documents the
   env-var contract, the plugin harness, the test layout, and the
   conventions every PR is reviewed against.
2. **Open an issue first** for non-trivial changes (anything that
   touches the harness, the state backend, the FastAPI surface, or
   public schemas). Trivial fixes (typos, doc dead links, single-line
   bug fixes) can go straight to a PR.
3. **Run `make check` before opening a PR.** It runs the fast gate
   (ruff lint + ruff format check + mypy + pytest fast tier) and is
   the same check CI runs.
4. **Use the PR template.** It asks for what, why, testing done, and
   a related issue link. Fill it in.
5. **Be patient.** This is a personal project with a single
   maintainer. Reviews can take a few days.

## What you can skip

You don't need to:

- Email ahead of time for trivial fixes.
- Sign a CLA. The project is MIT-licensed; your contribution falls
  under that licence by virtue of the GitHub Terms of Service.
- Run the slow test suite locally. The nightly workflow does that.
  Just run `make check`.

## Filing a bug

Use the
[`.github/ISSUE_TEMPLATE/bug_report.md`](.github/ISSUE_TEMPLATE/bug_report.md)
template. The maintainer triages within 10 business days per
[`docs/SECURITY.md`](docs/SECURITY.md); the same window applies to
regular bug reports.

## Filing a feature request

Use the
[`.github/ISSUE_TEMPLATE/feature_request.md`](.github/ISSUE_TEMPLATE/feature_request.md)
template. The maintainer reviews feature requests monthly; not every
request lands.

## Security issues

**Do not** open a public issue for security problems. See
[`docs/SECURITY.md`](docs/SECURITY.md) for the disclosure process.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
The TL;DR: be kind, assume good faith, and ask before you reformat
someone else's code.

## What "good" looks like for a PR

- **One logical change per PR.** A typo fix and a refactor don't
  belong in the same diff.
- **`make check` is green.** That's the contract.
- **Tests for new behaviour.** The coverage gate is 80%; the actual
  line you're adding should be exercised. A new endpoint without a
  test is a fast way to get a "needs tests" review.
- **Doc updates when behaviour changes.** If a user-facing behaviour
  changes, the change log (`docs/CHANGELOG.md`) and the relevant
  section of `docs/AGENTS.md` should reflect it in the same PR.
- **No drive-by reformats.** Don't mix a real change with `ruff
  format` rewrites of unrelated files; reviewers can't tell what to
  actually look at.

## Architecture decisions

The long-form architecture is in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The plugin harness is
the load-bearing decision: anything that has to plug in at boot
should be a plugin, not a hard-coded import. The state backend
Protocol is the second load-bearing decision: anything that
persists state goes through it.

If your change crosses either of these boundaries, expect a longer
review and possibly a design discussion before code lands.

## Local development loop

```bash
# One-time
uv sync --extra web --extra preprocessing
pre-commit install

# Day-to-day
make check          # fast gate (CI-equivalent)
make test-slow      # Surya + slow fixtures; ~5 min, runs against your HF cache
make doctor         # env / Redis / VLM health check
```

## Out of scope

- **Building a desktop binary** is on the Phase 4 roadmap
  ([remediation plan](docs/audits/2026-09-04-remediation-plan.md));
  until that RFC lands, please don't open PRs that touch
  `pyinstaller` / `fbs` / `flutter_dist` setup.
- **A web-hosted version of OmniScribe** is not in scope. The
  product is local-first by design.
- **Live LLM tests in CI** are explicitly excluded (the marker was
  removed in Wave 14). If you need to test a model interaction, run
  it locally against LM Studio / Ollama and document the model in
  your PR.

## See also

- [`README.md`](README.md) — product overview.
- [`docs/AGENTS.md`](docs/AGENTS.md) — the real contributor guide.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system architecture.
- [`docs/audits/2026-09-04-remediation-plan.md`](docs/audits/2026-09-04-remediation-plan.md) —
  the current roadmap.

_Last updated: 2026-09-05_
