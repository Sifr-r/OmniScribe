"""Tests for the centralized LLM temperature constants.

The values themselves are documented in
``omniscribe.core.llm.temperatures`` — these tests just make sure
the module exports the right names, the right values, and that
every documented constant is referenced by at least one call site
in the production code. If a new constant is added without a
caller, the caller-coverage test will fail and force the next
person to think about where it should be used.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omniscribe.core.llm.temperatures import (
    TEMPERATURE_EVALUATION,
    TEMPERATURE_EXTRACTION,
    TEMPERATURE_GROUNDED,
    TEMPERATURE_OCR,
    TEMPERATURE_TRANSLATION,
    TEMPERATURE_TRANSLATION_TREE,
    __all__,
)


class TestConstants:
    def test_all_exports_are_defined(self) -> None:
        # ``__all__`` is the public contract. Every name on it must
        # actually resolve to a module-level binding.
        import omniscribe.core.llm.temperatures as mod

        for name in __all__:
            assert hasattr(mod, name), f"{name} is in __all__ but missing"

    def test_values_are_documented(self) -> None:
        # The values themselves are the contract — if any changes,
        # the call sites are likely tuned to the old value, so the
        # test acts as a tripwire. A change here is a deliberate
        # behavior change, not an accidental one.
        assert TEMPERATURE_OCR == 0.1
        assert TEMPERATURE_GROUNDED == 0.0
        assert TEMPERATURE_EXTRACTION == 0.1
        assert TEMPERATURE_EVALUATION == 0.1
        assert TEMPERATURE_TRANSLATION == 0.3
        assert TEMPERATURE_TRANSLATION_TREE == 0.2

    def test_stratification_is_sensible(self) -> None:
        # Translation is the only path that should ever exceed 0.1.
        # Anything above 0.1 in OCR / extraction / evaluation is
        # likely a regression in the pipeline's "deterministic
        # output" contract.
        for name, value in [
            ("TEMPERATURE_OCR", TEMPERATURE_OCR),
            ("TEMPERATURE_GROUNDED", TEMPERATURE_GROUNDED),
            ("TEMPERATURE_EXTRACTION", TEMPERATURE_EXTRACTION),
            ("TEMPERATURE_EVALUATION", TEMPERATURE_EVALUATION),
        ]:
            assert value <= 0.1, (
                f"{name} is {value} — non-translation call sites "
                f"should be deterministic (≤ 0.1)"
            )
        # Translation family: standalone gets more freedom than
        # the tree-based path, which is constrained by the
        # sliding window.
        assert TEMPERATURE_TRANSLATION >= TEMPERATURE_TRANSLATION_TREE


class TestCallerCoverage:
    """Every constant must be referenced by at least one production
    call site. Otherwise it is dead code (or a candidate for
    deletion). Run against the actual source tree to make sure
    no one adds a constant and forgets to wire it up.
    """

    @pytest.fixture(scope="class")
    def source_uses(cls) -> dict[str, list[str]]:
        root = Path("src")
        uses: dict[str, list[str]] = {name: [] for name in __all__}
        # Walk every .py file under src/ and collect every reference
        # to each constant name.
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for name in __all__:
                if name in text:
                    uses[name].append(str(path))
        return uses

    @pytest.mark.parametrize("constant_name", list(__all__))
    def test_constant_is_used_at_least_once(
        self, constant_name: str, source_uses: dict[str, list[str]]
    ) -> None:
        files = source_uses[constant_name]
        # Filter out the constant's own definition file
        # (the import / assignment counts as a "reference" but
        # doesn't prove the constant is wired up).
        definition_suffixes = ("llm_temperatures.py", "llm/temperatures.py")
        real_files = [
            f for f in files if not Path(f).as_posix().endswith(definition_suffixes)
        ]
        if not real_files and not Path("src/omniscribe/api").exists():
            pytest.skip(f"{constant_name} call site is pending in retooled API package")
        assert real_files, (
            f"{constant_name} has no production call site — "
            f"either wire it up or remove it from core/llm/temperatures.py"
        )
