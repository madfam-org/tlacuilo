#!/usr/bin/env bash
# License gate for the runtime dependency set. tlacuilo itself is AGPL-3.0-only, so
# GPL-3/AGPL-3 dependencies are compatible but are REPORTED for review (PyMuPDF,
# Surya, Marker, Ghostscript-backed paths); non-free and source-available licences
# (SSPL, BUSL, Commons Clause, non-commercial) fail the build; unknown metadata is
# listed for a human to resolve.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python}"
"$PYTHON" -m piplicenses --format=json --with-system 2>/dev/null > /tmp/licenses.json || "$PYTHON" -m piplicenses --format=json > /tmp/licenses.json
"$PYTHON" - <<'PY'
import json, re, sys
rows = json.load(open("/tmp/licenses.json"))
ALLOW = re.compile(r"MIT|BSD|Apache|ISC|PSF|Python Software Foundation|MPL|Mozilla|Unlicense|Zlib|HPND|CC0|LGPL|Historical", re.I)
REVIEW = re.compile(r"AGPL|Affero|(?<!L)GPL", re.I)  # compatible with our AGPL-3.0, but a conscious choice
DENY = re.compile(r"SSPL|Server Side Public|BUSL|Business Source|Commons Clause|Non-?Commercial|NC\b|Proprietary", re.I)
# Packages whose PyPI metadata is empty/odd but whose license is known and permissive.
KNOWN = {"pdfminer.six": "MIT", "pypdfium2": "Apache-2.0 OR BSD-3-Clause", "typing_extensions": "PSF", "certifi": "MPL-2.0", "pip-licenses": "MIT", "tlacuilo": "MADFAM proprietary (this repo)"}
bad, unknown, review = [], [], []
for r in rows:
    name, lic = r["Name"], r.get("License") or ""
    lic = KNOWN.get(name, lic)
    if name.lower() in {"tlacuilo", "pip", "setuptools", "wheel", "pip-licenses", "prettytable", "wcwidth"}: continue
    if DENY.search(lic): bad.append((name, lic))
    elif REVIEW.search(lic): review.append((name, lic))
    elif not ALLOW.search(lic): unknown.append((name, lic))
for n, l in bad: print(f"DENIED   {n}: {l}")
for n, l in review: print(f"REVIEW   {n}: {l} (copyleft — compatible with AGPL-3.0, keep it deliberate)")
for n, l in unknown: print(f"UNKNOWN  {n}: {l or '(no metadata)'}")
if bad: sys.exit(1)
print(f"licenses: {len(rows)} packages checked, {len(review)} copyleft (review), {len(unknown)} without recognised metadata, 0 denied")
PY
