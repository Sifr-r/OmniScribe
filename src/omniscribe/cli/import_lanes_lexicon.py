"""CLI entry point for ``omniscribe-import-lanes-lexicon``.

Imports the Perseus/alpheios Lane's Arabic-English Lexicon (1863) into
the local LanceDB-backed glossary store. Two source formats are
supported:

* ``lanes_sqlite`` — the SQLite snapshot from
  https://github.com/laneslexicon/LexiconDatabase (~264 MB, 47,919 entries).
* ``lanes_xml`` — one or more TEI.2 XML files from
  https://github.com/laneslexicon/lexicon_xml (~63 MB, same corpus).

This script complements the legacy ``scripts/ingest_lexicon.py``:
where that script pulled only the *remote* GitHub copy and bypassed
the import pipeline, this CLI writes through the standard
``omniscribe.core.glossary_sources.parse(...)`` dispatch so the on-disk
LanceDB table gets the same shape (and same RAG fallback behaviour)
as every other glossary import.

Usage
-----

::

    omniscribe-import-lanes-lexicon --sqlite D:/Lanes/lexicon.sqlite/lexicon.sqlite
    omniscribe-import-lanes-lexicon --xml    D:/Lanes/lexicon_xml
    omniscribe-import-lanes-lexicon --xml    D:/Lanes/lexicon_xml --limit 1000
    omniscribe-import-lanes-lexicon --sqlite D:/Lanes/lexicon.sqlite/lexicon.sqlite --dry-run

The default ``--artifact-dir`` matches :mod:`omniscribe.cli.migrate_lexicon`:
``$OMNISCRIBE_ARTIFACT_DIR`` or ``./omniscribe_artifacts`` in the current
working directory. The LexiconStore is opened with
``LanceDBLexiconStore(path=...)`` which creates the directory if missing.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from omniscribe.core.glossary_sources import parse
from omniscribe.core.lexicon.lancedb_store import LanceDBLexiconStore

logger = logging.getLogger("omniscribe.cli.import_lanes_lexicon")


def _default_artifact_dir() -> Path:
    """Mirror :mod:`omniscribe.cli.migrate_lexicon`."""
    override = os.getenv("OMNISCRIBE_ARTIFACT_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.cwd() / "omniscribe_artifacts"


def _ascii_safe_repr(value: object) -> str:
    """Return ``repr(value)`` with non-ASCII characters replaced by ``?``.

    The dry-run output targets an interactive Windows terminal that
    defaults to cp1252; Arabic headwords and Lane's Arabic inline
    quotations in the sample preview would raise ``UnicodeEncodeError``
    on the eventual ``print()`` call even though ``repr()`` itself
    succeeds. We round-trip through ASCII so the *returned* string is
    guaranteed safe to print. Full Unicode round-trips are preserved
    in the parsed entries — this helper only sanitizes what we print.
    """
    text = repr(value)
    if not text.isascii():
        sanitized = text.encode("ascii", errors="replace").decode("ascii")
        length = len(text)
        return f"<{sanitized} [len={length}]>"
    return text


def _resolve_format(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Return ``(format_name, parser_kwargs)`` for the chosen source.

    Mutually-exclusive flags; the script refuses to run when both
    ``--sqlite`` and ``--xml`` are set (or neither) so a misconfigured
    invocation fails fast rather than silently picking a default.
    """
    if args.sqlite and args.xml:
        raise SystemExit("Specify only one of --sqlite or --xml (mutually exclusive).")
    if not args.sqlite and not args.xml:
        raise SystemExit("Specify either --sqlite <path> or --xml <path-or-dir>.")
    if args.sqlite:
        return (
            "lanes_sqlite",
            {"db_path": args.sqlite, "domain": args.domain},
        )
    return (
        "lanes_xml",
        {"xml_path": args.xml, "domain": args.domain},
    )


def _run_dry_run(
    *,
    format_name: str,
    parser_kwargs: dict[str, Any],
    limit: int | None,
) -> int:
    """Parse the source but do not write to LanceDB."""
    kwargs = dict(parser_kwargs)
    if limit is not None:
        kwargs["limit"] = limit
    summary = parse(format=format_name, **kwargs)
    print(
        "dry-run OK\n"
        f"  format:      {summary.format}\n"
        f"  source_uri:  {summary.source_uri}\n"
        f"  entries:     {summary.count}\n"
        f"  duplicates:  {summary.duplicates}\n"
        f"  warnings:    {len(summary.warnings)}"
    )
    if summary.entries:
        sample = summary.entries[0]
        # Strip non-ASCII from the sample preview so the dry-run output
        # prints cleanly on Windows terminals configured for cp1252.
        # The full Arabic / Lane gloss remains in the entry's `notes`
        # field, which the importer persists to LanceDB verbatim.
        source_repr = _ascii_safe_repr(sample.get("source"))
        target_repr = _ascii_safe_repr(sample.get("target"))
        print(
            "  sample entry:\n"
            f"    source:    {source_repr}\n"
            f"    target:    {target_repr}\n"
            f"    domain:    {sample.get('domain')!r}\n"
            f"    register:  {sample.get('register')!r}\n"
            f"    pos:       {sample.get('pos')!r}\n"
            f"    notes:     {_ascii_safe_repr(str(sample.get('notes', ''))[:80])}…"
        )
    return 0


def _run_ingest(
    *,
    format_name: str,
    parser_kwargs: dict[str, Any],
    name: str,
    artifact_dir: Path,
    limit: int | None,
) -> int:
    """Parse and persist the corpus to LanceDB."""
    store_path = artifact_dir / "lexicon.lance"
    kwargs = dict(parser_kwargs)
    if limit is not None:
        kwargs["limit"] = limit
    summary = parse(format=format_name, **kwargs)
    logger.info(
        "Parsed %d entries from %s (%s)",
        summary.count,
        summary.source_uri,
        format_name,
    )
    store = LanceDBLexiconStore(path=store_path)
    try:
        meta = store.save_glossary(
            name=name,
            format=format_name,
            entries=summary.entries,
            source_uri=summary.source_uri,
            encoding=summary.encoding,
            upsert=True,
        )
    finally:
        store.close()
    print(
        "ingest complete\n"
        f"  glossary_id:  {meta.id}\n"
        f"  name:         {meta.name}\n"
        f"  format:       {meta.format}\n"
        f"  entry_count:  {meta.entry_count}\n"
        f"  source_uri:   {meta.source_uri}\n"
        f"  artifact_dir: {artifact_dir}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="omniscribe-import-lanes-lexicon",
        description=(
            "Import Lane's Arabic-English Lexicon into the local "
            "LanceDB-backed glossary store."
        ),
    )
    parser.add_argument(
        "--sqlite",
        type=str,
        default=None,
        help=(
            "Path to the laneslexicon LexiconDatabase SQLite snapshot "
            "(e.g. D:/Lanes/lexicon.sqlite/lexicon.sqlite)."
        ),
    )
    parser.add_argument(
        "--xml",
        type=str,
        default=None,
        help=(
            "Path to a single TEI XML file or a directory of TEI XML "
            "files from the laneslexicon/lexicon_xml repository."
        ),
    )
    parser.add_argument(
        "--name",
        type=str,
        default="Lane's Lexicon",
        help="Glossary name shown in the library UI.",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default=None,
        help=(
            "Override the 'domain' field written to every entry "
            "(default: 'Lane's Lexicon')."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of entries to read (smoke-test convenience).",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=_default_artifact_dir(),
        help=(
            "Path to the OmniScribe artifact directory. "
            "Defaults to $OMNISCRIBE_ARTIFACT_DIR or ./omniscribe_artifacts."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse the source and print a sample; do not write to LanceDB.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose (INFO) logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    format_name, parser_kwargs = _resolve_format(args)
    if args.dry_run:
        return _run_dry_run(
            format_name=format_name,
            parser_kwargs=parser_kwargs,
            limit=args.limit,
        )
    return _run_ingest(
        format_name=format_name,
        parser_kwargs=parser_kwargs,
        name=args.name,
        artifact_dir=args.artifact_dir,
        limit=args.limit,
    )


if __name__ == "__main__":
    sys.exit(main())
