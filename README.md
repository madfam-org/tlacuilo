# tlacuilo

MADFAM's document-intelligence service: OCR and typed extraction for the documents the
ecosystem keeps re-discovering it needs to read — bank statements first (dhanam), then
receipts, SAT documents (karafiel), passports (meridian) and scanned forms (crea-map).

Free software under the [GNU AGPL-3.0](./LICENSE). Landing: <https://tlacuilo.madfam.io>.
Ruled 2026-09-05 (design record in MADFAM's private internal-devops RFC 0040): standalone,
Python, no third-party cloud for restricted documents, substrate not SKU, public under AGPL.
Agent rules and doctrines: [AGENTS.md](./AGENTS.md). Security: [SECURITY.md](./SECURITY.md).

## What it returns

A `document-extraction/v1` result (`contracts/document-extraction.v1.json`): the document's
hashes and classification, provenance (engines, parser, timings), a typed `statement`
with per-row confidence, page and box, and a `validation` block. Consumers may
auto-commit rows only when `validation.balanceReconciles` is true.

## Run locally

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
TLACUILO_ENV=local AUTH_DISABLED=true .venv/bin/uvicorn tlacuilo.api:app --port 8000
curl -s -X POST localhost:8000/v1/extractions -H 'X-Sensitivity: restricted' -F file=@statement.pdf | jq .validation
```

`AUTH_DISABLED` is honoured only when `TLACUILO_ENV` is `local` or `test`. In the cluster
every call carries a Janua RS256 service token with the role `tlacuilo:extract` and an
`X-Sensitivity` header from the closed enum `public | internal | confidential | restricted`.

## M0 scope (this repo today)

- Text-layer PDFs (pdfplumber), generic Mexican statement table parser, bank fingerprints,
  running-balance validation, synthetic corpus generator with ground truth (`tests/synth.py`).
- Sync mode (multipart, ≤ `SYNC_MAX_PAGES`) and async mode (caller's presigned R2 URL,
  Celery worker, one-shot result, HMAC-signed callback).
- The landing at `tlacuilo.madfam.io` (`web/`), the only public surface; the API is in-cluster.
- Not yet: OCR of scans (Tesseract is installed in the image for M1), per-bank parsers,
  the Selva VLM lane (M3), receipts / CSF / MRZ (M3–M4).
