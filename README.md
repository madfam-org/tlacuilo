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

## Production status: landing live, api and worker not launched yet

In production only the landing runs. The api and worker Deployments and the
`tlacuilo-config` ExternalSecret are kept in `infra/k8s/production/` as **spec only, not
wired**: they are commented out of `kustomization.yaml`, and `tests/test_manifests.py`
asserts that. Wired before their Secrets existed, their pods could never start and the
ExternalSecret could never sync, so the Argo Application stayed Degraded and could not
signal a real fault. The `tlacuilo` Service, NetworkPolicies, ServiceMonitor and
PrometheusRule stay wired; a Service with no endpoints is healthy.

### Launching the api and worker

Do these in order. Each step is an operator step; no value enters this repo.

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
5. **Launch.** Uncomment `api-deployment.yaml` and `worker-deployment.yaml` (both declare
   `replicas: 1`) and flip `test_api_worker_and_external_secret_are_not_wired_until_launch`
   in the same PR. Argo creates the Deployments; `/ready` turns green once the database
   pool knows the new role.

Steps 4 and 5 can share one PR only if steps 1–3 are already done. Do not use
`replicas` in git to park or unpark a live Deployment: the Application ignores
`/spec/replicas` on existing Deployments, so only creation honours it.
