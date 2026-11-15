# tlacuilo

MADFAM's document-intelligence service: OCR and typed extraction for the documents the
ecosystem keeps re-discovering it needs to read — bank statements first (dhanam), then
receipts, SAT documents (karafiel), passports (meridian) and scanned forms.

Free software under the [GNU AGPL-3.0](./LICENSE). Landing: <https://tlacuilo.madfam.io>.
Ruled 2026-09-05 (design record in MADFAM's private internal-devops RFC 0040): standalone,
Python, no third-party cloud for restricted documents, substrate not SKU, public under AGPL.
Agent rules and doctrines: [AGENTS.md](./AGENTS.md). Security: [SECURITY.md](./SECURITY.md).

## What it returns

A `document-extraction/v1` result (`contracts/document-extraction.v1.json`): the document's
hashes and classification, provenance (engines, parser, timings), a typed `statement`
with per-row confidence, page and box, and a `validation` block. Consumers may
auto-commit rows only when `validation.balanceReconciles` is true.

For a document that is not a statement, the **letterhead lane** returns an `issuer`
block instead of a `statement`: who issued the document and when.

```json
"issuer": {
  "institution":  { "name": "…", "confidence": 0.9, "page": 1, "bbox": [x0, top, x1, bottom] },
  "professional": { "name": "…", "confidence": 0.9, "page": 1, "bbox": [x0, top, x1, bottom] },
  "documentDate": { "date": "2026-11-12", "confidence": 0.9, "page": 1, "bbox": [x0, top, x1, bottom] }
}
```

- A caller asks for it explicitly: the form field `document_type=letterhead_document` on
  `POST /v1/extractions`. Without it nothing changes — a statement result has exactly the
  keys it always had, and the `issuer` key appears only on results of this lane.
- Rules only: no model is called and nothing leaves the pod, at any sensitivity.
- Each field is `null` when the rules do not find it; a result with three nulls is a valid
  result with a warning in `validation.warnings`. The lane never guesses.
- A name is returned as written. A professional licence number, an address, a phone and
  an e-mail are never returned, and the block has no free-text field for one to travel in.
- It reads the letterhead and the signature block of the first two pages. Text-layer PDFs
  are read directly; a scanned PDF, a JPEG or a PNG goes through OCR (confidences are
  lower and `provenance.engines.ocr` names the engine). Boxes are PDF points for a PDF and
  pixels of the image for an image.

For a **Constancia de Situación Fiscal**, the **csf lane** returns a `csf` block: every
field the SAT prints (identity, domicilio fiscal by part, padrón facts, lugar y fecha de
emisión), the régimen, actividades and obligaciones tables, the document's QR code and
the head of its «Cadena Original Sello» as cross-checks, and a `quality` verdict.

```json
"csf": {
  "legalForm": { "value": "persona_fisica", "confidence": 0.95 },
  "fields": {
    "rfc": { "value": "XAXP900101AB1", "status": "read", "confidence": 0.97, "lowConfidence": false,
             "source": "ocr", "page": 1, "bbox": [x0, top, x1, bottom], "checks": ["format_ok", "qr_agrees"] },
    "interiorNumber": { "value": null, "status": "blank", "confidence": 0.5, "lowConfidence": true, … },
    …
  },
  "regimenes": [ { "name": "Régimen Simplificado de Confianza", "startDate": "2022-01-01", "endDate": null, … } ],
  "qr":   { "status": "decoded", "url": "https://siat.sat.gob.mx/…", "satHost": true, "idCif": "…", "rfc": "…" },
  "seal": { "status": "read", "timestamp": "2026-11-12T10:15:30", "rfc": "…" },
  "quality": { "verdict": "good", "source": "ocr", "lowConfidenceFields": [], "unreadFields": [], … }
}
```

- `document_type=csf` selects it. A text-layer PDF is read directly; a scan, a photo
  (JPEG, PNG, HEIC/HEIF) or a PDF that is a picture under a scanner app's invisible text
  is read by OCR after the page is prepared (orientation, contrast, skew, size, noise;
  `tlacuilo/engines/scan.py`). `reader=ocr` asks for OCR even when there is a text layer:
  a consumer's second reading of what the text layer did not yield.
- Values are read **by region, not by line order**: the page-1 header of a real
  constancia interleaves two blocks and wraps the emission phrase across them.
- Every value is a reading: a status (`read`, `invalid`, `blank`, `not_found`,
  `not_applicable`), a confidence, `lowConfidence` under 0.6, the source, the page, the
  box and the checks it passed. The QR code (decoded, **never fetched**) and the seal
  corroborate the RFC, the idCIF and the emission date — or pull a field down when they
  disagree. Nothing is corrected or defaulted. Every value is confirmed by the person in
  the consumer's interface; `lowConfidence` says which to present as doubtful.
- Rules only: no model is called and nothing leaves the pod, at any sensitivity. A CSF
  is a tax-identity document: callers send `X-Sensitivity: restricted`.
- At most four pages are read, 60 s of OCR at most per document (one OCR job per pod at
  a time); a limit is an error code. Accuracy on the synthetic corpus, per field and
  degradation level: `python -m tests.csf_accuracy` (`tests/synth_csf.py` renders three
  invented taxpayers and spoils them as scans and photos).

## Run locally

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
TLACUILO_ENV=local AUTH_DISABLED=true .venv/bin/uvicorn tlacuilo.api:app --port 8000
curl -s -X POST localhost:8000/v1/extractions -H 'X-Sensitivity: restricted' -F file=@statement.pdf | jq .validation
curl -s -X POST localhost:8000/v1/extractions -H 'X-Sensitivity: restricted' \
  -F document_type=letterhead_document -F file=@letter.pdf | jq .issuer
curl -s -X POST localhost:8000/v1/extractions -H 'X-Sensitivity: restricted' \
  -F document_type=csf -F file=@synthetic-constancia.jpg | jq .csf.quality
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
- The letterhead lane (RFC 0040 amendment 1): institution, professional and document date
  by rules (`tlacuilo/parsers/letterhead.py`), with OCR for this lane only
  (`tlacuilo/engines/ocr.py`: Tesseract, Spanish, CPU, at most two pages, PDF / JPEG / PNG)
  and a synthetic letter generator with ground truth (`tests/synth_letter.py`).
- The csf lane (RFC 0040 amendment 2, pending its record in internal-devops): every
  field of a Constancia de Situación Fiscal by rules, OCR for scans and photos with page
  preparation, the QR code and seal as cross-checks (`tlacuilo/parsers/csf*.py`,
  `tlacuilo/engines/scan.py`, `tlacuilo/csf_contract.py`) and a synthetic corpus with
  degradations (`tests/synth_csf.py`, `tests/csf_accuracy.py`).
- Not yet: OCR of statement scans (M1 — a scanned statement still answers `needs_ocr`),
  per-bank parsers, the Selva VLM lane (M3), receipts / MRZ (M3–M4). See
  [ROADMAP.md](./ROADMAP.md).

Error codes are closed and a limit is never a partial answer: `unsupported_mime`,
`unreadable_pdf`, `too_many_pages`, `needs_ocr`; for the letterhead and csf lanes
`unreadable_image`, `image_too_large`, `unreadable_scan`, `ocr_unavailable`,
`ocr_timeout`, `ocr_busy`, `ocr_failed`; for the csf lane `not_a_csf` and
`invalid_reader`.

## Production status: landing live, api and worker not launched yet

In production only the landing runs. The api and worker Deployments and the
`tlacuilo-config` ExternalSecret are kept in `infra/k8s/production/` as **spec only, not
wired**: they are commented out of `kustomization.yaml`, and `tests/test_manifests.py`
asserts that. Wired before their Secrets existed, their pods could never start and the
ExternalSecret could never sync, so the Argo Application stayed Degraded and could not
signal a real fault. The `tlacuilo` Service, NetworkPolicies, ServiceMonitor and
PrometheusRule stay wired; a Service with no endpoints is healthy.

### Launching the api and worker

Two paths. Each step is an operator step; no value enters this repo.

#### Sync-only: the api alone

The api needs one Secret to start — the database's. Its queue Secret is optional in
`api-deployment.yaml`, so the api can launch before the worker, the queue, the gateway
token and the callback key exist. It then serves sync mode (the statement lane and the
letterhead lane); an async request answers 503 `queue_unconfigured`, and `/ready`
reports `redis: unconfigured`.

1. **Database.** Provision the Enclii postgres addon so the Secret `tlacuilo-secrets`
   (`DATABASE_URL`) exists in the `tlacuilo` namespace.
2. **Identity.** Register the Janua service clients that call the api (role
   `tlacuilo:extract`). A consumer also opens its own egress to `tlacuilo:80`.
3. **Launch.** One PR, two lines: uncomment `api-deployment.yaml` in
   `infra/k8s/production/kustomization.yaml` (add `- api-deployment.yaml` under
   `resources:`) and set `API_LAUNCHED = True` in `tests/test_manifests.py`. Argo creates
   the Deployment; `/ready` turns green once the database pool knows the new role.

The worker and the ExternalSecret stay spec only. When they launch later, follow the
full path from step 2; a running api reads the new Secret at its next rollout.

#### Full: api, worker and queue

Do these in order.

1. **Database.** Provision the Enclii postgres addon so the Secret `tlacuilo-secrets`
   (`DATABASE_URL`) exists in the `tlacuilo` namespace.
2. **Vault `secret/tlacuilo`.** Add Enclii secret-intake targets for its four properties
   and write them through the intake:
   - `callback_hmac_key`: an internal random key (`--generate`).
   - `selva_api_key`: issued by Selva's client provisioning.
   - `celery_broker_url` and `celery_result_backend`: need a Redis decision first.
3. **Identity.** Register the Janua service clients that call the api (role
   `tlacuilo:extract`).
4. **Wire the ExternalSecret.** Uncomment `external-secret.yaml` in `kustomization.yaml`
   and confirm `ExternalSecret tlacuilo-config` is `Ready=True` after its first refresh.
5. **Launch.** Uncomment `worker-deployment.yaml` (and `api-deployment.yaml`, unless the
   sync-only path already wired it; both declare `replicas: 1`) and set `QUEUE_LAUNCHED`
   (and `API_LAUNCHED`) to `True` in `tests/test_manifests.py` in the same PR. Argo
   creates the Deployments; `/ready` turns green once the database pool knows the new role.

Steps 4 and 5 can share one PR only if steps 1–3 are already done. Do not use
`replicas` in git to park or unpark a live Deployment: the Application ignores
`/spec/replicas` on existing Deployments, so only creation honours it.
