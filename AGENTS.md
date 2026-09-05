# AGENTS.md — tlacuilo

> **Boundary note (public-safe).** This is a public repository. Operational detail that
> would reveal MADFAM's posture — cluster topology, secret paths, break-glass procedures,
> operator runbooks — lives in the private `internal-devops` repository and is only
> *pointed to* from here. Agents working in this repo: never copy that detail in.

Read this before touching the repo. Every agent commit ends with
`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## What tlacuilo is

The MADFAM ecosystem's document-intelligence service (design record: internal-devops
RFC 0040): capture → classify → text layer or OCR → layout → typed parsing → validation →
a schema-versioned, confidence-scored `document-extraction/v1` result with provenance.
Consumers: dhanam (bank statements into the ledger), karafiel (receipts, SAT documents),
later meridian (passport MRZ), crea-map (scanned forms), tezca (scanned gazettes).

Ruled by the owner on 2026-09-05: standalone service · Python · no third-party cloud for
restricted documents · substrate, not a catalog SKU · **public repository under
AGPL-3.0-only** · a landing at `tlacuilo.madfam.io` while the API stays in-cluster.
Name: *tlacuilo*, the Nahuatl codex scribe.

## The four doctrines (locked; CI enforces what it can)

1. **Nothing durable.** Document bytes exist in worker memory and the pod's `emptyDir`
   for one job. The only table (`jobs`) holds ids, hashes, sizes, pages, timings, engine
   names, status and the sensitivity label — never text, fields or bytes; rows expire
   (`JOB_TTL_SECONDS`). Async results are held one-shot in Redis for `RESULT_TTL_SECONDS`
   and deleted on first read; that is the M0 compromise (Redis may persist briefly) and it
   is documented, not hidden. Originals stay in the caller's own storage. Adding a
   content column is a doctrine change — it needs an RFC amendment, not a migration.
2. **One path to models, no provider keys, no hosted OCR.** Any model call goes through
   the ecosystem inference gateway (Selva) with the caller's `X-Sensitivity` propagated
   verbatim; `restricted` routes to a local model or fails. `scripts/check-selva-contract.sh`
   fails CI on any provider SDK, key name, or hosted OCR / document-AI reference.
   Known-layout parsers never call a model at all.
3. **The API is in-cluster only; the landing is the only public surface.** No `domains:`
   on the API service and no tunnel NetworkPolicy peer for API pods — CI asserts the
   tunnel rule selects `tlacuilo-web` alone. `allow-consumer-ingress` names the namespaces
   that may call. A public API hostname requires the owner to rule productization first.
4. **Fail closed, never a stub.** An unreadable document is an error with a code
   (`needs_ocr`, `unsupported_mime`, `unreadable_pdf`, `too_many_pages`). A result that does
   not reconcile is still a result — `validation.balanceReconciles: false` — and the
   consumer routes it to review. Nothing in this repo may invent a row.

Also locked: **logs carry no content** (`tlacuilo/logging.py` masks digit runs; the
pipeline logs only counts, timings and codes — `tests/test_redaction.py` pins it) and
**licences are deliberate** (`scripts/check-licenses.sh`: permissive by default; GPL/AGPL
dependencies are compatible with our AGPL-3.0 but are reported for a conscious choice —
PyMuPDF, Surya, Marker and Ghostscript-backed paths; source-available and non-commercial
licences fail the build).

## What tlacuilo does not own

Storage of originals (each consumer's own buckets), compliance sealing and retention
(karafiel's NOM-151 seal, dhanam's retention tiers), the ledger (dhanam `Transaction`),
identity (Janua), inference routing and cost (Selva), generative GPU work (ceq). The
service owns *extractions*, not *documents*.

## Layout

- `tlacuilo/api.py` — FastAPI: `/health`, `/ready`, `/metrics`, `POST /v1/extractions`
  (sync multipart | async presigned URL), `GET /v1/extractions/{id}`.
- `tlacuilo/pipeline.py` — one job end to end; raises `ExtractionError` with a code.
- `tlacuilo/engines/text_layer.py` — pdfplumber words → lines with boxes. OCR lane: M1.
- `tlacuilo/parsers/` — `common.py` (Mexican amounts and dates), `registry.py` (bank
  fingerprints; M1 adds `mx.<bank>.<product>.v<n>` parsers), `generic_statement.py`
  (header columns, running balance, sign inference).
- `tlacuilo/validate.py` — opening + Σ rows = closing, the only auto-commit signal.
- `tlacuilo/contract.py` — the pydantic models; `contracts/document-extraction.v1.json`
  is generated from them (`python -m tlacuilo.cli schema`) and CI fails on drift. The
  ecosystem's published copy must stay byte-identical.
- `tlacuilo/worker.py` — Celery on the shared queue, one-shot result store, HMAC callback.
  `tlacuilo/db.py` — the jobs table (created at startup in M0; Alembic arrives with M1).
- `tests/synth.py` — synthetic statements with ground truth; the accuracy harness seed.
- `web/` — the landing (static HTML, Caddy, non-root, `/health`).
- `infra/k8s/production/` — Deployments (api, worker, web), Services, NetworkPolicies,
  the ExternalSecret for queue and gateway credentials, ServiceMonitor, PrometheusRule.
- `scripts/` — the three CI guards (gateway contract, licence gate, secret coverage).

## Deploy shape (public-safe summary)

Deployed through the Enclii platform: one project, two services (`tlacuilo` API on port
80 → 8000, in-cluster; `tlacuilo-web` landing on port 80 → 8080 at `tlacuilo.madfam.io`),
a managed Postgres for job metadata, images built and signed by the shared reusable
workflow (`.github/workflows/build-deploy.yml`) and digest-pinned into
`infra/k8s/production/kustomization.yaml`. Secrets never enter this repo: queue and
gateway credentials arrive through the platform's secret intake into the ExternalSecret
target; the database URL is written by the platform's Postgres addon. Operator steps and
their gotchas are console gates in internal-devops (family `O`), not documentation here.

Things that bite (public-safe):

- First build: the reusable workflow skips when there is no previous commit; a second
  commit touching `infra/k8s/production/` triggers it.
- The pooled database URL works only once the pool knows the new role; `/ready` fails until
  then — that is the signal, not a bug.
- Consumers must open *their* egress to `tlacuilo:80`; the API's own policies only admit
  their namespaces.

## Working here

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check . && .venv/bin/pytest -q
scripts/check-selva-contract.sh && PYTHON=.venv/bin/python scripts/check-licenses.sh
.venv/bin/python scripts/check-secret-coverage.py && .venv/bin/python -m tlacuilo.cli check-schema
```

Conventional commits; PR bodies end with the Claude Code line; CI must be green.
