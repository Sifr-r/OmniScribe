"""Optional SQL pair-table glossary importer."""

from __future__ import annotations

import logging
import re
from typing import Any

from ._common import entry_dict, finalize, validate_identifier
from .summary import FormatNotAvailableError, GlossaryImportSummary, redact_dsn

logger = logging.getLogger(__name__)
_MAX_ROWS = 1_000_000
_WHERE_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*(=|!=|<>|<=|>=|<|>|LIKE)\s*"
    r"(?:'([^']*)'|\"([^\"]*)\"|(-?\d+(?:\.\d+)?)|NULL)$",
    re.IGNORECASE,
)


def parse_sql_table(
    *,
    dsn: str,
    source_table: str,
    source_col: str,
    target_col: str,
    target_table: str | None = None,
    where_clause: str | None = None,
    encoding: str | None = None,
) -> GlossaryImportSummary:
    """Read source/target pairs with safe identifiers and bound predicates."""
    try:
        from sqlalchemy import create_engine, text
    except ImportError as exc:
        raise FormatNotAvailableError(
            "SQL import requires SQLAlchemy. Install with: "
            "pip install omniscribe[glossary]"
        ) from exc

    clean_dsn = str(dsn).strip()
    if not clean_dsn:
        raise ValueError("SQL DSN is required.")
    if clean_dsn.startswith("sqlite3://"):
        clean_dsn = "sqlite://" + clean_dsn[len("sqlite3://") :]
    table = validate_identifier(source_table, "source_table")
    source = validate_identifier(source_col, "source_col")
    target = validate_identifier(target_col, "target_col")
    if target_table is not None and target_table != source_table:
        raise ValueError("source_table and target_table must match for a pair table.")
    logger.info("Reading glossary SQL table from %s", redact_dsn(clean_dsn))

    from omniscribe.utils.security import is_blocked_host

    try:
        from sqlalchemy.engine import make_url

        url = make_url(clean_dsn)
        if (
            url.get_backend_name() != "sqlite"
            and url.host
            and is_blocked_host(url.host)
        ):
            raise ValueError(
                f"Access to private or local host '{url.host}' is forbidden."
            )
    except ValueError:
        raise
    except Exception:
        pass

    try:
        engine = create_engine(clean_dsn)
    except Exception as exc:
        raise ValueError("Could not open the glossary SQL data source.") from exc

    try:
        predicate, parameters = _build_where(where_clause)
        # SQL injection is prevented via three layers:
        # 1. validate_identifier() in _common.py enforces regex ^[A-Za-z_][A-Za-z0-9_]*$ on table/column names
        # 2. engine.dialect.identifier_preparer.quote() quotes identifiers per database dialect
        # 3. WHERE clause uses bound parameters via .bindparams(**parameters)
        # No user input is directly concatenated into the query.
        quoted_table = engine.dialect.identifier_preparer.quote(table)
        quoted_source = engine.dialect.identifier_preparer.quote(source)
        quoted_target = engine.dialect.identifier_preparer.quote(target)
        statement = text(  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
            f"SELECT {quoted_source} AS source, {quoted_target} AS target "
            f"FROM {quoted_table}{predicate}"
        ).bindparams(**parameters)
        entries: list[dict[str, object]] = []
        with engine.connect() as connection:
            result = connection.execute(statement)
            for row in result:
                if len(entries) >= _MAX_ROWS:
                    raise ValueError(
                        f"SQL glossary contains more than {_MAX_ROWS:,} rows."
                    )
                mapping: Any = row._mapping
                item = entry_dict(mapping.get("source"), mapping.get("target"))
                if item is not None:
                    entries.append(item)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Could not read the glossary SQL table.") from exc
    finally:
        engine.dispose()

    if not entries:
        raise ValueError("SQL glossary table contains no valid pairs.")
    return finalize(
        entries,
        format_name="sql_table",
        encoding=encoding,
    )


def _build_where(where_clause: str | None) -> tuple[str, dict[str, object]]:
    if where_clause is None or not where_clause.strip():
        return "", {}
    clauses = re.split(r"\s+AND\s+", where_clause.strip(), flags=re.IGNORECASE)
    rendered: list[str] = []
    params: dict[str, object] = {}
    for index, clause in enumerate(clauses):
        match = _WHERE_RE.fullmatch(clause.strip())
        if match is None:
            raise ValueError(
                "where_clause supports only simple AND predicates with bound values."
            )
        column, operator, single, double, number = match.groups()
        quoted_column = f'"{validate_identifier(column, "where_clause")}"'
        if single is None and double is None and number is None:
            rendered.append(f"{quoted_column} IS NULL")
            continue
        parameter = f"glossary_where_{index}"
        raw_value: object = single if single is not None else double
        if number is not None:
            raw_value = float(number) if "." in number else int(number)
        rendered.append(f"{quoted_column} {operator} :{parameter}")
        params[parameter] = raw_value
    return " WHERE " + " AND ".join(rendered), params
