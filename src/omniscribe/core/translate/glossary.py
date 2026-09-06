"""Glossary support for translation.

A glossary is a list of source→target term pairs that the translation prompt
injects verbatim (DeepL-style ``style_rules``). Terms are matched
case-insensitively by default, with longest-match-first priority to avoid
partial overlaps.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from omniscribe.utils.prompt_safety import sanitize_prompt_input


@dataclass(slots=True)
class GlossaryEntry:
    source: str
    target: str
    case_sensitive: bool = False
    notes: str = ""
    source_uri: str | None = None
    encoding: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "case_sensitive": self.case_sensitive,
            "notes": self.notes,
        }


@dataclass(slots=True)
class Glossary:
    entries: list[GlossaryEntry] = field(default_factory=list)
    source_uri: str | None = None
    source_format: str | None = None
    encoding: str | None = None

    def is_empty(self) -> bool:
        return not any(e.source.strip() for e in self.entries)

    def to_prompt_block(self) -> str:
        """Return a DeepL-style ``style_rules`` prompt block.

        Returns an empty string when the glossary is empty. Entries are
        sanitized here — they originate from user-uploaded imports, so a
        crafted entry must not inject instructions into the prompt.
        """
        active = [e for e in self.entries if e.source.strip() and e.target.strip()]
        if not active:
            return ""
        # Longest first so the LLM prefers the longest matching term.
        active.sort(key=lambda e: len(e.source), reverse=True)
        lines = ["GLOSSARY (use these exact translations for the listed terms):"]
        for e in active:
            source = sanitize_prompt_input(e.source)
            target = sanitize_prompt_input(e.target)
            lines.append(f"- {source} -> {target}")
        return "\n".join(lines)

    def apply_to_text(self, text: str) -> str:
        """Apply the glossary to ``text`` using deterministic case-insensitive
        (or case-sensitive) substitution. This is best-effort: the LLM should
        still be told about the glossary via :meth:`to_prompt_block`.
        """
        if not text:
            return text
        out = text
        for e in sorted(self.entries, key=lambda x: len(x.source), reverse=True):
            if not e.source.strip():
                continue
            flags = 0 if e.case_sensitive else re.IGNORECASE
            pattern = re.escape(e.source)
            out = re.sub(pattern, e.target, out, flags=flags)
        return out

    def to_dict(self) -> dict[str, object]:
        return {"entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Glossary:
        raw = data.get("entries") or []
        if not isinstance(raw, list):
            return cls()
        entries: list[GlossaryEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            src = str(item.get("source", "")).strip()
            tgt = str(item.get("target", "")).strip()
            if not src or not tgt:
                continue
            entries.append(
                GlossaryEntry(
                    source=src,
                    target=tgt,
                    case_sensitive=bool(item.get("case_sensitive", False)),
                    notes=str(item.get("notes", "")),
                )
            )
        return cls(entries=entries)

    @classmethod
    def from_dict_with_metadata(cls, data: dict[str, object]) -> Glossary:
        """Build a glossary and capture source metadata at the dictionary level."""
        glos = cls.from_dict(data)
        if isinstance(data, dict):
            uri = data.get("source_uri")
            fmt = data.get("source_format")
            enc = data.get("encoding")
            glos.source_uri = str(uri) if uri else None
            glos.source_format = str(fmt) if fmt else None
            glos.encoding = str(enc) if enc else None
        return glos

    @classmethod
    def from_paired_lines(cls, text: str) -> Glossary:
        """Parse ``source = target`` per line."""
        entries: list[GlossaryEntry] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            src, tgt = line.split("=", 1)
            src = src.strip()
            tgt = tgt.strip()
            if src and tgt:
                entries.append(GlossaryEntry(source=src, target=tgt))
        return cls(entries=entries)

    @classmethod
    def merge(cls, glossaries: Iterable[Glossary]) -> Glossary:
        """Merge multiple glossaries, later entries overriding earlier ones."""
        seen: dict[str, GlossaryEntry] = {}
        for g in glossaries:
            for e in g.entries:
                key = e.source.lower()
                seen[key] = e
        return cls(entries=list(seen.values()))
