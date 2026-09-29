"""Lane's Arabic-English Lexicon glossary parsers.

Two formats share this module because they describe the same corpus
(Perseus TEI XML + alpheios-project SQLite snapshot of the same text):

``lanes_sqlite`` — read the SQLite snapshot at :mod:`sqlite3` and emit
one glossary entry per row in ``entry`` (joined with ``pos`` for part-of-
speech tags). Each row carries the Arabic headword (``word``), the
romanized form (``bword``), the unique node id (``nodeid``), the
Lane-style page reference, and the full English definition embedded in
the ``xml`` column.

``lanes_xml`` — read one or more TEI XML files (Perseus/alpheios
edition) and emit one glossary entry per ``<entryFree>`` element. Useful
when only the raw XML bundle is available.

In both formats the per-entry ``source`` is the Arabic headword and
``target`` is the *first concise Lane gloss* — the first
``<hi rend="ital">…</hi>`` text up to the first comma, e.g.
``Mighty, potent, powerful,``. Lane's pattern is "A shower, or fall,
or what pours forth at once…" so the first comma usually closes the
initial sense definition. The full multi-paragraph definition (and all
sub-senses, quotations, and cross-references) goes into ``notes`` so
the translation prompt block stays compact while RAG lookup can still
consult the full text via vector similarity over ``source_text`` (the
headword). ``case_sensitive=True`` because Arabic and romanized forms
must not collide during case-fold normalization.

Security: both parsers refuse anything that is not a local filesystem
path and require the path to exist and point at a regular file (SQLite)
or a file/directory of ``.xml`` files (XML). The XML parser uses
:mod:`defusedxml` (already a base dep, see audit P1-8) so DTDs and
external entities are rejected at the expat level.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ._common import (
    XmlElement,
    finalize,
    local_name,
    safe_xml_root,
)
from .summary import (
    FormatNotAvailableError,
    GlossaryImportSummary,
)

logger = logging.getLogger(__name__)

_SOURCE_LANG = "ara"
_TARGET_LANG = "eng"
_DOMAIN = "Lane's Lexicon"
_NOTES_PREFIX = "Lane's Arabic-English Lexicon"

# Lane's TEI uses TEI.2 / custom namespaces; ``entryFree`` is the entry
# wrapper. The Arabic headword lives under ``<form>/<orth lang="ar">``.
# English glosses are marked with ``<hi rend="ital">`` (Perseus TEI.2
# convention; not the TEI P5 ``<hi>`` semantic).

_MAX_ROWS = 1_000_000


@dataclass(frozen=True, slots=True)
class _LocalPath:
    """A validated local filesystem path (no URLs, no remote)."""

    raw: str
    path: Path

    @property
    def is_file(self) -> bool:
        return self.path.is_file()

    @property
    def is_dir(self) -> bool:
        return self.path.is_dir()


def _validate_local_path(value: str, field_name: str) -> _LocalPath:
    """Reject URLs, env vars, shell tricks — only accept a real local path."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required.")
    # Reject URL-shaped inputs early so callers can't smuggle an SSRF path.
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", raw) or raw.startswith("//"):
        raise ValueError(f"{field_name} must be a local filesystem path.")
    # ``os.path.expandvars`` would let ``%TEMP%/foo`` resolve through
    # env vars; we want a literal path the caller explicitly chose.
    if "%" in raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ValueError(f"{field_name} contains invalid characters.")
    path = Path(raw).expanduser()
    if not path.exists():
        raise ValueError(f"{field_name} does not exist: {path}")
    if not path.is_file() and not path.is_dir():
        raise ValueError(f"{field_name} is not a regular file or directory: {path}")
    return _LocalPath(raw=raw, path=path)


# ---------------------------------------------------------------------------
# SQLite adapter
# ---------------------------------------------------------------------------


def parse_lanes_lexicon_sqlite(
    *,
    db_path: str,
    domain: str | None = None,
    limit: int | None = None,
) -> GlossaryImportSummary:
    """Read Lane's Lexicon entries from a local SQLite snapshot.

    Parameters
    ----------
    db_path:
        Absolute or user-relative path to the ``lexicon.sqlite`` file
        shipped by https://github.com/laneslexicon/LexiconDatabase.
    domain:
        Optional override for the ``domain`` field written to every
        entry. Defaults to ``"Lane's Lexicon"``.
    limit:
        Optional cap on the number of rows to read. Useful for smoke
        tests; production callers should leave it ``None``.
    """
    try:
        import sqlite3 as _sqlite3
    except ImportError as exc:  # pragma: no cover - stdlib always present
        raise FormatNotAvailableError(
            "SQLite support is unavailable in this Python build."
        ) from exc
    del _sqlite3  # imported only to validate availability

    validated = _validate_local_path(db_path, "lanes_sqlite_path")
    if not validated.is_file:
        raise ValueError(
            f"lanes_sqlite_path must point at a file (got directory): {validated.path}"
        )
    effective_limit = int(limit) if limit is not None else _MAX_ROWS
    if effective_limit < 1 or effective_limit > _MAX_ROWS:
        raise ValueError(f"limit must be between 1 and {_MAX_ROWS:,}.")
    effective_domain = str(domain or _DOMAIN).strip() or _DOMAIN
    logger.info(
        "Reading Lane's Lexicon SQLite at %s (limit=%s)",
        validated.path,
        effective_limit,
    )

    entries: list[dict[str, object]] = []
    seen_node_ids: set[str] = set()
    try:
        # ``mode=ro`` + ``uri=True`` opens the database read-only and
        # guarantees the importer cannot mutate the source. The legacy
        # ``ingest_lexicon.py`` script used ``r/w``; that's a destructive
        # footgun for a 264 MB local snapshot.
        connection = sqlite3.connect(
            f"file:{validated.path.as_posix()}?mode=ro", uri=True
        )
    except sqlite3.Error as exc:
        raise ValueError(
            f"Could not open Lane's Lexicon SQLite database: {exc}"
        ) from exc
    try:
        connection.row_factory = sqlite3.Row
        try:
            cur = connection.execute(
                """
                SELECT e.nodeid, e.word, e.bword, e.itype, e.page,
                       e.xml, e.root, p.pos
                  FROM entry e
                  LEFT JOIN pos p ON p.nodeid = e.nodeid
                 WHERE e.word IS NOT NULL AND e.word != ''
                 ORDER BY e.id
                 LIMIT ?
                """,
                (effective_limit,),
            )
        except sqlite3.Error as exc:
            raise ValueError(
                "Could not query Lane's Lexicon SQLite database; "
                "is this the laneslexicon/LexiconDatabase schema?"
            ) from exc

        for row in cur:
            node_id = (row["nodeid"] or "").strip()
            headword = (row["word"] or "").strip()
            if not node_id or not headword:
                continue
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)

            entry_xml = row["xml"] or ""
            first_gloss = _extract_first_gloss(entry_xml)
            full_text = _extract_full_text(entry_xml)
            notes_body = _build_notes(
                full_text=full_text,
                first_gloss=first_gloss,
                bword=(row["bword"] or "").strip(),
                page=row["page"],
            )

            entry: dict[str, object] = {
                "id": f"lane_{node_id}",
                "source": headword,
                "target": first_gloss or headword,
                "case_sensitive": True,
                "notes": notes_body,
                "source_lang": _SOURCE_LANG,
                "target_lang": _TARGET_LANG,
                "domain": effective_domain,
                "register": (row["itype"] or "").strip() or None,
                "pos": ((row["pos"] or "").strip() or None),
            }
            entries.append(entry)
    finally:
        connection.close()

    if not entries:
        raise ValueError(
            "Lane's Lexicon SQLite contains no bilingual entries "
            "(expected rows in the 'entry' table)."
        )
    return finalize(
        entries,
        format_name="lanes_sqlite",
        encoding="utf-8",
        source_uri=validated.path.as_posix(),
    )


# ---------------------------------------------------------------------------
# XML adapter
# ---------------------------------------------------------------------------


def parse_lanes_lexicon_xml(
    *,
    xml_path: str,
    domain: str | None = None,
    limit: int | None = None,
) -> GlossaryImportSummary:
    """Read Lane's Lexicon ``<entryFree>`` blocks from local TEI XML files.

    Parameters
    ----------
    xml_path:
        Either a single ``.xml`` file or a directory containing one or
        more ``.xml`` files (Perseus/alpheios edition).
    domain:
        Optional override for the ``domain`` field. Defaults to
        ``"Lane's Lexicon"``.
    limit:
        Optional cap on the number of entries to emit.
    """
    validated = _validate_local_path(xml_path, "lanes_xml_path")
    files = _resolve_xml_files(validated.path)
    if not files:
        raise ValueError(f"lanes_xml_path has no .xml files under it: {validated.path}")
    effective_limit = int(limit) if limit is not None else _MAX_ROWS
    if effective_limit < 1 or effective_limit > _MAX_ROWS:
        raise ValueError(f"limit must be between 1 and {_MAX_ROWS:,}.")
    effective_domain = str(domain or _DOMAIN).strip() or _DOMAIN
    logger.info(
        "Reading Lane's Lexicon XML from %s (%d file(s), limit=%s)",
        validated.path,
        len(files),
        effective_limit,
    )

    entries: list[dict[str, object]] = []
    seen_node_ids: set[str] = set()
    for path in files:
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Could not read Lane's XML file {path}: {exc}") from exc
        try:
            root = safe_xml_root(raw_bytes)
        except ValueError:
            raise
        except Exception as exc:  # pragma: no cover - defusedxml is strict
            raise ValueError(f"Invalid Lane's Lexicon XML in {path}: {exc}") from exc

        for entry_element in root.iter():
            if local_name(entry_element.tag) != "entryfree":
                continue
            headword = _entry_headword(entry_element)
            if not headword:
                continue
            node_id = entry_element.attrib.get("id") or ""
            if not node_id:
                # Some TEI files omit ``id``; fall back to the headword
                # so we still emit an entry. Stable across re-imports
                # because the headword is unique within a Lane file.
                node_id = f"lane_xml_{path.stem}_{headword}"
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)

            full_text = " ".join(_entry_text_fragments(entry_element)).strip()
            first_gloss = _first_hi_gloss(entry_element)
            notes_body = _build_notes(
                full_text=full_text,
                first_gloss=first_gloss,
                bword=_entry_buckwalter(entry_element),
                page=None,
            )
            entry: dict[str, object] = {
                "id": f"lane_xml_{node_id}"
                if not node_id.startswith("lane_")
                else node_id,
                "source": headword,
                "target": first_gloss or headword,
                "case_sensitive": True,
                "notes": notes_body,
                "source_lang": _SOURCE_LANG,
                "target_lang": _TARGET_LANG,
                "domain": effective_domain,
                "register": _entry_itype(entry_element) or None,
            }
            entries.append(entry)
            if len(entries) >= effective_limit:
                break
        if len(entries) >= effective_limit:
            break

    if not entries:
        raise ValueError(
            "Lane's Lexicon XML contains no <entryFree> entries "
            "(expected TEI.2 files with <entryFree> children)."
        )
    return finalize(
        entries,
        format_name="lanes_xml",
        encoding="utf-8",
        source_uri=validated.path.as_posix(),
    )


def _resolve_xml_files(path: Path) -> list[Path]:
    """Return the list of XML files to walk, sorted for determinism."""
    if path.is_file():
        return [path]
    return sorted(
        p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".xml"
    )


def _entry_headword(entry: XmlElement) -> str:
    """Return the first Arabic headword inside an ``<entryFree>``."""
    first_non_empty_orth = ""
    for form in entry.iter():
        if local_name(form.tag) != "form":
            continue
        for orth in form:
            if local_name(orth.tag) != "orth":
                continue
            text = (orth.text or "").strip()
            if text and text != "*":
                if _looks_like_arabic(text):
                    return text
                if not first_non_empty_orth:
                    first_non_empty_orth = text
    if first_non_empty_orth:
        return first_non_empty_orth
    # Fallback: TEI ``key`` attribute carries the headword for entries
    # whose ``<form>`` is empty (rare but observed).
    key = entry.attrib.get("key") or ""
    return key.strip()


def _entry_itype(entry: XmlElement) -> str:
    """Return the verb-form tag (``<itype>``) if any."""
    for child in entry.iter():
        if local_name(child.tag) == "itype":
            value = (child.text or "").strip()
            if value:
                return value
    return ""


def _entry_text_fragments(entry: XmlElement) -> Iterable[str]:
    """Yield the entry's display text in document order, skipping ``<orth>``.

    Performs a recursive in-order document traversal over the XML element:
    - For element elem: if local_name(elem.tag) == "orth", skip.
    - If local_name(elem.tag) not in {"entryfree", "form"}: yield (elem.text or "").strip() if non-empty.
    - For each child in elem: recursively walk child, then yield (child.tail or "").strip() if non-empty and child.tag not in {"form"}.
    This ensures mixed text and tails are emitted in exact document order and nested tails are preserved.
    """

    def _walk(elem: XmlElement) -> Iterator[str]:
        if local_name(elem.tag) == "orth":
            return
        if local_name(elem.tag) not in {"entryfree", "form"}:
            text = (elem.text or "").strip()
            if text:
                yield text
        for child in elem:
            yield from _walk(child)
            tail = (child.tail or "").strip()
            if tail and local_name(child.tag) not in {"form"}:
                yield tail

    return _walk(entry)


def _entry_buckwalter(entry: XmlElement) -> str:
    """Best-effort extraction of the romanized headword from ``<orth>``."""
    for form in entry.iter():
        if local_name(form.tag) != "form":
            continue
        for orth in form:
            if local_name(orth.tag) != "orth":
                continue
            text = (orth.text or "").strip()
            if text and text != "*" and not _looks_like_arabic(text):
                return text
    return ""


def _first_hi_gloss(entry: XmlElement) -> str:
    """Extract Lane's first English gloss from the first ``<hi rend="ital">``.

    Lane's pattern is ``<hi rend="ital">A shower,</hi> or
    ``<hi rend="ital">fall,</hi>…``. We grab the first ``<hi>`` and
    cut at the first comma (the conventional end of the first sense).
    Falls back to the first non-Arabic text fragment if no ``<hi>`` is
    found.
    """
    for child in entry.iter():
        if local_name(child.tag) != "hi":
            continue
        if child.attrib.get("rend", "").lower() != "ital":
            continue
        text = "".join(child.itertext()).strip()
        if not text:
            continue
        # First comma marks the end of the initial sense in Lane's style.
        for separator in (",", ";", ":"):
            if separator in text:
                first, _rest = text.split(separator, 1)
                first = first.strip()
                if first:
                    return first
        return text.rstrip(".")
    # Fallback: first non-Arabic text fragment from the entry.
    for child in entry.iter():
        if local_name(child.tag) in {"entryfree", "form", "orth", "itype", "langusage"}:
            continue
        text = "".join(child.itertext()).strip()
        if text and not _looks_like_arabic(text):
            return text.split(",", 1)[0].strip()
    return ""


def _extract_first_gloss(entry_xml: str) -> str:
    """Pull the first English gloss out of a stored ``xml`` column."""
    if not entry_xml:
        return ""
    try:
        root = safe_xml_root(entry_xml.encode("utf-8"))
    except ValueError:
        return ""
    return _first_hi_gloss(root)


def _extract_full_text(entry_xml: str) -> str:
    """Pull the full definition text out of a stored ``xml`` column."""
    if not entry_xml:
        return ""
    try:
        root = safe_xml_root(entry_xml.encode("utf-8"))
    except ValueError:
        return ""
    return " ".join(_entry_text_fragments(root)).strip()


def _build_notes(
    *,
    full_text: str,
    first_gloss: str,
    bword: str,
    page: int | None,
) -> str:
    """Compose the ``notes`` block stored on each entry.

    We deliberately keep this short:

    * one-line gloss (so a quick read of the library preview is useful);
    * the romanized headword if available;
    * the page reference if available;
    * the full Lane definition text.

    The translation prompt only consumes ``source -> target``; the
    full text lives here so hybrid search and RAG can surface it.
    """
    header_lines: list[str] = [_NOTES_PREFIX]
    if first_gloss:
        header_lines.append(f"Gloss: {first_gloss}")
    if bword:
        header_lines.append(f"Buckwalter: {bword}")
    if page is not None:
        header_lines.append(f"Lane page: {page}")
    header_lines.append("")
    if full_text:
        return "\n".join(header_lines) + full_text
    return "\n".join(header_lines).rstrip()


_ARABIC_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)


def _looks_like_arabic(text: str) -> bool:
    """Cheap script check — true if the string contains Arabic codepoints."""
    return bool(_ARABIC_RE.search(text))


# ---------------------------------------------------------------------------
# Internal: re-export the symbols we expose at the package level.
# ---------------------------------------------------------------------------

__all__ = [
    "parse_lanes_lexicon_sqlite",
    "parse_lanes_lexicon_xml",
]
