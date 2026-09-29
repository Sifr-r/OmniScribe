# OmniScribe — Screenshots & demo media

This directory contains the screenshots embedded in the root README and
the remaining demo-media capture target.

## What goes here

| File | What it shows | Source |
| --- | --- | --- |
| `workstation.png` | The Workstation screen of the Flutter client with the sample PDF loaded (pipeline dock visible). | `client/lib/presentation/workstation/workstation_screen.dart` |
| `workstation-empty.png` | The Workstation upload dropzone (empty state). | `client/lib/presentation/workstation/controls/upload_dropzone.dart` |
| `ai-setup-wizard-modal.png` | The AI Engine Setup Wizard (Choose Mode → Configure Connection → Ready). | `client/lib/presentation/providers/ai_setup_wizard_modal.dart` |
| `ai-setup-wizard.png` | The provider configuration step (endpoint / key / model / test). | `client/lib/presentation/providers/provider_modal.dart` |
| `ai-provider-browser.png` | The LLM provider browser with all catalogued endpoints. | `client/lib/presentation/providers/provider_modal.dart` |
| `glossary-screen.png` | The Terminology Glossary screen (Libraries / Entries / Merged Lexicon). | `client/lib/presentation/features/glossary_screen.dart` |
| `export-modal.png` | The Export Document modal with the searchable-PDF format selector. | `client/lib/presentation/workstation/modals/export_modal.dart` |
| `terminal-server-up.png` | `uv run omniscribe-server` startup banner: the `state backend sqlite` line, the 14-plugin harness mount, and `Uvicorn running`. | Rendered from the real server startup log (not a screen photo); provenance note below. |
| `drop-to-result.gif` | The end-to-end flow: drop a PDF, watch OCR progress, preview the searchable result. | A 10-second screen recording. |

All filenames are lowercased, hyphen-separated, no spaces. The
root `README.md` embeds `workstation.png`, `ai-setup-wizard-modal.png`,
`glossary-screen.png`, and `export-modal.png` in a Screenshots strip
near the top.

## Status (2026-09-27)

Eight of the nine assets above now exist. The seven UI captures were
taken headlessly: real API server (`uv run omniscribe-server`) +
Flutter web client (`flutter run -d web-server`) + Playwright against
the app's semantics tree (booted with `?a11y=1`, which calls
`SemanticsBinding.ensureSemantics()` from `lib/main.dart`). The
sample document is the server's own `/api/sample-pdf/digital.pdf`
fixture — no VLM was needed because the captures show pre-OCR
states. `terminal-server-up.png` is rendered programmatically from
the server's genuine startup log (Pillow over `consola.ttf`), so the
banner lines are verbatim real output, not a screen photo. Still
missing (needs a live VLM run + screen recording):
`drop-to-result.gif`, the 10-second OCR-pass recording.

## Capture the remaining demo

1. Start the Flutter client (`flutter run -d windows`) and a live VLM,
   then load a sample PDF from `examples/`.
2. Use Windows Game Bar (Win + G) or
   OBS to record 10 seconds of "drop PDF → progress bar → result".
   Convert to GIF with `ffmpeg -i recording.mp4 -vf
   "fps=15,scale=800:-1" drop-to-result.gif`. Aim for <2 MB.

## Out of scope for this directory

- Architecture diagrams (those go in `../ARCHITECTURE.md`).
- Tutorial videos beyond the short in-repository demo GIF.
- A demo dataset for the screenshots — `examples/` already ships
  CC0 PDFs that work for the capture.

_Last updated: 2026-09-27_
