"""Prompt constants and selection helpers for the OCR processor.

Architecture overview
--------------------

The OCR path has two distinct message-shape contracts:

1.  **Canonical page prompt (OLMOCR_PAGE_PROMPT)** — a string the
    OlmOCR-2 model was RL-trained on verbatim. It's sent as a
    pure user message; adding a system role shifts the model's
    input distribution and the LM Studio endpoint returns
    LLMCallError. ``tests/core/ocr/test_ocr.py::TestPromptConstants::
    test_olmocr_prompt_is_canonical`` pins the exact body —
    do not edit it without re-validating downstream OCR quality.

2.  **Everything else** (crop / handwriting / dual-engine /
    correction / grounded) — we send a system message carrying
    the role identity + cross-cutting rules (diacritics
    emphasis, "don't invent text", "return empty on blank
    regions", "image is the source of truth for the
    dual-engine judge") and the user message carries only
    the task-specific instructions + the image.

The split exists because the canonical OLMOCR prompt is
sacred but other code paths benefit from identity / rules
separation. ``model_supports_system_role`` gates the
non-canonical path: a small allow-list excludes models that
have demonstrated sensitivity to a layered system role
(currently just OlmOCR-2 / OlmOCR / older OlmOCR). Unknown
model names default to "system message OK" so we don't
silently disable system messages for new additions.

``select_system_message`` and the ``_resolve_page_system``
/ ``_resolve_crop_system`` helpers on OCRProcessor are the
single source of truth for "which system message goes with
which call site". If you're adding a new code path that
calls ``call_llm`` for OCR, use one of those helpers — do
not invent a new system role at the call site.

``PROMPT_VERSION`` is bumped on any user-visible body
change. The date + version format (``YYYY-MM-DD.vN``) is
asserted by ``tests/core/ocr/test_ocr.py::test_prompt_version_is_present``.
"""

from __future__ import annotations

from omniscribe.utils.prompt_safety import sanitize_prompt_input

# Bump when the user-facing prompt body changes so log/runtime telemetry
# can correlate regressions with a known version. The body strings are
# versioned together — a version bump means at least one constant in
# this module changed in a user-visible way.
PROMPT_VERSION = "2026-08-15.v1"

# Models whose RL training distribution expects a single user-role turn
# (the OLMOCR prompt). Sending a system role on top of the canonical
# prompt shifts the distribution and triggers LLMCallError on LM Studio.
# Keep this list narrow — only include models that have *demonstrated*
# the issue in production. Add to it as new models surface.
_MODELS_WITHOUT_SYSTEM_ROLE = frozenset(
    {
        "allenai/olmocr-2-7b",
        "allenai/olmocr-7b-0225-preview",
        "olmocr",
    }
)


def model_supports_system_role(model_name: str | None) -> bool:
    """Return True when the configured model can safely receive a system message.

    Some models (notably OlmOCR-2) were RL-trained on a single
    user-role turn with the canonical OlmOCR prompt body. Adding a
    system role on top of that shifts the input distribution and
    causes LM Studio to misbehave on the crop / handwriting /
    dual-engine paths. Detection is intentionally conservative:
    only models that have *demonstrated* the issue are excluded
    from system messages. Unknown / unrecognized model names
    default to ``True`` so we don't accidentally disable system
    messages for new models we haven't seen.
    """
    if not model_name:
        return True
    name = model_name.lower()
    return "olmocr" not in name and name not in _MODELS_WITHOUT_SYSTEM_ROLE


# Canonical OlmOCR-2 prompt (the model was RL-trained on this exact string).
# Source: github.com/allenai/olmocr olmocr/prompts/prompts.py
# :func:`build_no_anchoring_v4_yaml_prompt`.
#
# NOTE: This is the model's training distribution. Do NOT wrap it in a
# system message, do NOT add suffixes, and do NOT touch the body. The
# test_olmocr_prompt_is_canonical test in tests/core/ocr/test_ocr.py locks the
# exact string. Call sites send it as a plain user message.
OLMOCR_PAGE_PROMPT = (
    "Attached is one page of a document that you must process. Just return "
    "the plain text representation of this document as if you were reading it "
    "naturally. Convert equations to LateX and tables to HTML.\n"
    "If there are any figures or charts, label them with the following "
    "markdown syntax ![Alt text describing the contents of the figure]"
    "(page_startx_starty_width_height.png)\n"
    "Return your output as markdown, with a front matter section on top "
    "specifying values for the primary_language, is_rotation_valid, "
    "rotation_correction, is_table, and is_diagram parameters."
)

# Companion system message for crop OCR. Prepended to the messages array
# so the role identity sits in the system role (not competing with task
# content in the user turn). The user content then carries only the
# crop-specific instructions and the image.
OCR_SYSTEM_MESSAGE = (
    "You are a highly accurate OCR assistant. "
    "Pay extremely close attention to all diacritical marks "
    "(e.g. Arabic Tashkeel, French accents, German umlauts). "
    "They are deliberate and must be transcribed accurately; "
    "do not dismiss them as speckles or background noise. "
    "If a region is blank or unreadable, emit nothing for it. "
    "Do not invent text to fill space."
)

# Handwriting-tuned system message. The base OCR_SYSTEM_MESSAGE says
# "don't invent text"; this one goes a step further on the patience
# axis and asks the model to prefer empty output over a guess.
HANDWRITING_OCR_SYSTEM_MESSAGE = (
    "You are a highly accurate OCR assistant specialized in HANDWRITTEN text "
    "(cursive, print, or a mix). "
    "Pay extremely close attention to diacritics (accents, umlauts, "
    "Arabic tashkeel). "
    "If a region is blank or you cannot read it confidently, return empty "
    "rather than guess. "
    "Preserve crossed-out / strikethrough words using "
    "`[crossed: original text]` syntax."
)

# System message for the dual-engine path (Tesseract draft + VLM
# correction). The role here is "judge" rather than "transcriber" —
# the user turn supplies the draft and image.
DUAL_ENGINE_OCR_SYSTEM_MESSAGE = (
    "You are a highly accurate OCR assistant. "
    "You will be given a rough draft transcription from a legacy OCR engine "
    "and the original image. "
    "Treat the image as the source of truth and use the draft only as a hint. "
    "Correct hallucinations, missing diacritics, and wrong characters in "
    "the draft. Pay extremely close attention to all diacritical marks."
)

# Prompt for cropped box regions — we want raw text only, no metadata
# (the YAML front matter is nonsensical for a single line/region).
CROP_PROMPT = """
Extract all text from the provided image crop exactly as it appears.
Output only the plain text with no markdown formatting or other commentary.
"""

DUAL_ENGINE_PAGE_PROMPT = """
Extract all text from the provided page image exactly as it appears.
You are given a rough, error-prone draft transcription from a legacy OCR engine. Use it as a hint, but rely on the image for the final correct text, paying special attention to correcting hallucinations and diacritics in the draft.

DRAFT HINT:
{draft_text}

Output only the corrected transcribed text, without any conversational formatting or commentary.
"""

DUAL_ENGINE_CROP_PROMPT = """
Extract all text from the provided image crop exactly as it appears.
You are given a rough draft transcription from a legacy OCR engine. Use it as a hint, but rely on the image for the final correct text. The draft may include text from elsewhere on the page that is NOT in this crop; ignore any draft lines that don't match the visible image, and transcribe only what is actually in this crop.

DRAFT HINT:
{draft_text}

Output only the corrected transcribed text, without any conversational formatting or commentary.
"""

CORRECTION_PAGE_PROMPT = (
    "Attached is an image of a document and a draft transcription. The draft may contain minor "
    "errors such as missing diacritics, incorrect characters, or hallucinated words (especially in Arabic script). "
    "Carefully compare the draft to the image and output the perfectly corrected text in the same format.\n"
    "Draft Transcription:\n"
    "---\n"
    "{draft_text}\n"
    "---\n"
    "Please provide only the corrected transcription with no additional commentary."
)

CORRECTION_CROP_PROMPT = (
    "Attached is an image of a cropped text region and a draft transcription. "
    "Carefully compare the draft to the image and output the perfectly corrected text on a single line. "
    "Fix any missing diacritics or incorrect characters (especially for Arabic script). "
    "Output only the plain text with no explanation.\n"
    "Draft Transcription: {draft_text}"
)


# Handwriting-specific page prompt. The base OLMOCR_PAGE_PROMPT assumes
# printed text; this variant nudges the model to handle cursive, ascenders,
# descenders, and crossed-out words without dropping them.
HANDWRITING_PAGE_PROMPT = (
    "Attached is one page of a document that may contain HANDWRITTEN text "
    "(cursive, print, or a mix). Read it as carefully as you would a personal "
    "letter. Pay close attention to:\n"
    "  - Ascenders and descenders that may be ambiguous (b/d/p/q, n/u, etc.).\n"
    "  - Words that may be misjoined or split incorrectly across lines.\n"
    "  - Crossed-out or strikethrough words: keep them in the output as "
    "[crossed: original text].\n"
    "  - Margin annotations, arrows, and numbered notes: include them in reading order.\n"
    "Return the page as Markdown. Tables -> HTML. Equations -> LaTeX.\n"
    "Use a YAML front matter as in the printed-text prompt."
)

# Handwriting-specific crop prompt. Used when per-region OCR is requested.
HANDWRITING_CROP_PROMPT = (
    "You are recognizing a single line of HANDWRITTEN text from a document.\n"
    "Be patient with cursive, irregular spacing, and ambiguous characters.\n"
    "Pay attention to diacritics (accents, umlauts, Arabic tashkeel).\n"
    "If you cannot read the line, return an empty string rather than guess.\n"
    "Output only the plain text with no markdown formatting."
)


def select_page_prompt(handwriting_mode: bool = False) -> str:
    """Return the page-level prompt configured by the caller."""
    return HANDWRITING_PAGE_PROMPT if handwriting_mode else OLMOCR_PAGE_PROMPT


def select_crop_prompt(handwriting_mode: bool = False) -> str:
    """Return the crop-level prompt configured by the caller."""
    return HANDWRITING_CROP_PROMPT if handwriting_mode else CROP_PROMPT


def select_system_message(
    *,
    handwriting_mode: bool = False,
    dual_engine: bool = False,
) -> str | None:
    """Return the appropriate system message for the configured call.

    Returns ``None`` for the canonical OLMOCR-2 page path: the model
    was RL-trained on the prompt as a plain user message, so a system
    role would shift the distribution. All other paths get a
    purpose-built system message.
    """
    if dual_engine:
        return DUAL_ENGINE_OCR_SYSTEM_MESSAGE
    if handwriting_mode:
        return HANDWRITING_OCR_SYSTEM_MESSAGE
    return OCR_SYSTEM_MESSAGE


def fill_dual_engine_page(draft_text: str) -> str:
    """Substitute the Tesseract draft into the dual-engine page prompt."""
    return DUAL_ENGINE_PAGE_PROMPT.replace(
        "{draft_text}", sanitize_prompt_input(draft_text)
    )


def fill_dual_engine_crop(draft_text: str) -> str:
    """Substitute the Tesseract draft into the dual-engine crop prompt."""
    return DUAL_ENGINE_CROP_PROMPT.replace(
        "{draft_text}", sanitize_prompt_input(draft_text)
    )


def fill_correction_page(draft_text: str) -> str:
    """Substitute the draft text into the correction page prompt."""
    return CORRECTION_PAGE_PROMPT.replace(
        "{draft_text}", sanitize_prompt_input(draft_text)
    )


def fill_correction_crop(draft_text: str) -> str:
    """Substitute the draft text into the correction crop prompt."""
    return CORRECTION_CROP_PROMPT.replace(
        "{draft_text}", sanitize_prompt_input(draft_text)
    )
