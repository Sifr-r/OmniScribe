"""Summarize semgrep SARIF findings in a clean format."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sarif_path = Path("semgrep.sarif")
data = json.loads(sarif_path.read_text(encoding="utf-8"))
runs = data.get("runs", [])
if not runs:
    print("No runs in SARIF.")
    sys.exit(0)

# Build rule metadata lookup
rules_by_id: dict[str, dict] = {}
for r in runs[0].get("tool", {}).get("driver", {}).get("rules", []):
    rules_by_id[r.get("id", "")] = r

results = runs[0].get("results", [])
print(f"Total findings: {len(results)}\n")

for i, finding in enumerate(results, 1):
    rule_id = finding.get("ruleId", "")
    rule_meta = rules_by_id.get(rule_id, {})
    short = rule_meta.get("shortDescription", {}).get("text", "") or rule_meta.get("fullDescription", {}).get("text", "")
    severity = rule_meta.get("defaultConfiguration", {}).get("level") or finding.get("level", "")
    msg = finding.get("message", {}).get("text", "")[:400]
    loc = finding.get("locations", [{}])[0].get("physicalLocation", {})
    uri = loc.get("artifactLocation", {}).get("uri", "")
    line = loc.get("region", {}).get("startLine", "")

    print(f"=== Finding {i} ===")
    print(f"  Rule    : {rule_id}")
    print(f"  Severity: {severity}")
    print(f"  Summary : {short}")
    print(f"  Location: {uri}:{line}")
    print(f"  Message : {msg}")
    print()
