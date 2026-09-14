# OmniScribe — Screenshots & demo media

The audit's end-user lens (U6) called out the absence of any
screenshots, GIFs, or recordings. This directory is the convention
for those assets.

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
| `drop-to-result.png` | The end-to-end flow: drop a PDF, watch OCR progress, preview the searchable result. | A 10-second screen recording; static screenshot optional. |

All filenames are lowercased, hyphen-separated, no spaces. The
root `README.md` embeds `workstation.png`, `ai-setup-wizard-modal.png`,
`glossary-screen.png`, and `export-modal.png` in a Screenshots strip
near the top.

## Status (2026-09-13)

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
`drop-to-result.png`, the 10-second OCR-pass recording.

## How to capture (manual, single-machine)

1. **Workstation:** start the Flutter client (`flutter run -d
   windows`). Drag a sample PDF from `examples/` onto the
   workstation. Take a screenshot when the OCR is in progress (the
   progress bar) and another when the result is ready. Save as
   `workstation.png` (resolution: at least 1280×720).

2. **Settings:** in the Flutter client, click the **Settings** tab.
   Take a screenshot showing the VLM endpoint and the Advanced
   Configuration panel. Save as `settings.png`.

3. **Drop-to-result recording:** use Windows Game Bar (Win + G) or
   OBS to record 10 seconds of "drop PDF → progress bar → result".
   Convert to GIF with `ffmpeg -i recording.mp4 -vf
   "fps=15,scale=800:-1" drop-to-result.gif`. Aim for <2 MB.

4. **Terminal-server-up:** start the server with the SQLite default
   (Phase 2.3). Take a screenshot of the terminal showing the
   `omniscribe state_backend=sqlite` log line and the `Uvicorn
   running on ...` line. Save as `terminal-server-up.png`.

## Why a convention, not captures

The captures depend on a running VLM endpoint and a built Flutter
client. Either of those takes 30+ minutes to set up on a fresh
machine (see [`../TROUBLESHOOTING.md`](../TROUBLESHOOTING.md)). The
audit persona can't run the captures themselves; the project
maintainer has to. So this directory is a landing pad: the next
maintainer who has a working install runs the four steps above
and the README's screenshots are no longer 404s.

## Out of scope for this directory

- Architecture diagrams (those go in `../ARCHITECTURE.md`).
- Tutorial videos (would need a hosting plan; out of scope until
  the binary distribution lands).
- A demo dataset for the screenshots — `examples/` already ships
  CC0 PDFs that work for the capture.

_Last updated: 2026-09-13_
