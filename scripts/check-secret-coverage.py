#!/usr/bin/env python3
"""Every configuration key the service reads must be declared in the production
manifests — as a plain env var, an ExternalSecret data key, or a key the Enclii
Postgres addon writes. A key read by code but declared nowhere is the class of
outage dhanam's SELVA_API_KEY comment describes (read, never provisioned, silently
degraded). Run from the repo root."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tlacuilo.settings import Settings  # noqa: E402

ADDON_KEYS = {"DATABASE_URL", "DIRECT_DATABASE_URL"}  # written by the Enclii postgres addon
manifests = "\n".join(p.read_text() for p in (ROOT / "infra/k8s/production").glob("*.yaml"))
declared = set(re.findall(r"^\s*-\s*name:\s*([A-Z][A-Z0-9_]+)\s*$", manifests, flags=re.M))
declared |= set(re.findall(r"^\s*-\s*secretKey:\s*([A-Z][A-Z0-9_]+)\s*$", manifests, flags=re.M))
declared |= ADDON_KEYS

missing = []
for name, field in Settings.model_fields.items():
    extra = field.json_schema_extra or {}
    if extra.get("source") == "code":  # test-only / derived keys never come from the cluster
        continue
    env = name.upper()
    if env not in declared:
        missing.append(env)
if missing:
    print("secret-coverage: keys read by tlacuilo.settings but declared in no manifest:")
    for m in missing:
        print(f"  - {m}")
    sys.exit(1)
print(f"secret-coverage: {len(Settings.model_fields)} settings keys, all declared or addon-provided")
