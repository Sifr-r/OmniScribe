"""Read-only checks for feature ownership and local Dart imports.

Run from the workspace: python client/tool/check_feature_layout.py.
This is a source-layout check, not a Dart analyzer replacement.
"""

import re
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[1]
LIB = CLIENT / "lib"
paths = [
    path
    for tree in ("lib", "test", "integration_test", "tool")
    for path in (CLIENT / tree).rglob("*.dart")
]
providers: dict[str, list[Path]] = {}
for path in paths:
    text = path.read_text(encoding="utf-8")
    for uri in re.findall(r"(?:import|export) '([^']+)'", text):
        if uri.startswith("package:omniscribe_client/"):
            target = LIB / uri.removeprefix("package:omniscribe_client/")
        elif not uri.startswith(("package:", "dart:")):
            target = path.parent / uri
        else:
            continue
        assert target.is_file(), f"{path}: missing {uri}"
    if path.is_relative_to(LIB) and path.parts[len(LIB.parts)] not in ("data",):
        assert not re.search(
            r"(?:import|export) 'package:omniscribe_client/(data|presentation)/", text
        ), path
        assert "featureRepositoryProvider" not in text, path
        assert "FeatureRepository" not in text, path
    if path.is_relative_to(LIB):
        for provider in re.findall(r"^final (\w+Provider)\s*=", text, re.MULTILINE):
            providers.setdefault(provider, []).append(path)
    if path.name.endswith(("_screen.dart", "_modal.dart")) and path.is_relative_to(
        LIB / "features"
    ):
        assert not re.search(r"import '[^']*(?:api_client|_repository)\.dart'", text), (
            path
        )

assert all(len(owners) == 1 for owners in providers.values()), providers
for domain in (
    "workstation",
    "settings",
    "providers",
    "jobs",
    "translation",
    "transcription",
    "glossary",
    "documents",
):
    assert (LIB / "features" / domain).is_dir(), domain
for domain in ("translation", "transcription", "glossary", "documents"):
    stem = "document" if domain == "documents" else domain
    assert (LIB / "features" / domain / f"{stem}_repository.dart").is_file(), domain
assert not re.search(
    r"(?:import|export) '[^']*/features/",
    (LIB / "shared/providers/api_providers.dart").read_text(encoding="utf-8"),
)
print(
    f"Feature layout passed: {len(paths)} Dart files, {len(providers)} unique provider declarations."
)
