# ROADMAP — tlacuilo

The design record is internal-devops RFC 0040 (private); this file is the public status.
A row moves only with the code, tests and docs that make it true.

| Milestone | What | Status |
|---|---|---|
| M0 | Scaffold: text-layer PDFs, generic statement parser, contract `document-extraction/v1`, sync + async modes, Janua auth, CI guards, landing | Done (2026-09). API and worker **not running in production** — see below |
| A1 | Letterhead lane: issuer and date by rules, OCR for it (Tesseract `spa`, two pages) | Done (2026-11-11) |
| A2 | CSF lane: a Constancia de Situación Fiscal field by field, by region; OCR of scans and photos with page preparation (orientation, contrast, skew, size, noise); QR code (zxing-cpp) and seal as cross-checks; per-field confidence and a quality verdict; synthetic corpus with degradation levels and an accuracy harness | Built (this branch); the amendment is recorded in internal-devops as a follow-up. First consumer: Karafiel's CSF intake (behind its `CSF_OCR_ENABLED` flag) |
| M1 | Bank statements: per-bank parsers, OCR of statement scans | Not started |
| M2 | Dhanam integration and battle test (consent text and DPIA gates T4/T5 first) | Not started |
| M3 | Selva VLM lane (`restricted` routes to a local model or fails); receipts, CFDI PDFs, 32-D | Not started |
| M4 | MRZ, scanned forms, gazettes | Not started |

## Engines: chosen, and not (yet)

- **Tesseract 5 `spa`** (Apache-2.0, the image's Debian package) is the OCR engine of both
  lanes, run as a subprocess over stdin/stdout. **pypdfium2** rasterises PDFs (never
  PyMuPDF, AGPL).
- **zxing-cpp** (Apache-2.0) decodes QR codes. **pi-heif** (LGPL-3.0, decode-only libheif)
  opens HEIC/HEIF photos.
- **RapidOCR** (RFC 0040's «quality lane» for photos) is *not wired*. Its licences pass
  `scripts/check-licenses.sh` (rapidocr-onnxruntime Apache-2.0, onnxruntime MIT,
  opencv-python Apache-2.0, numpy/shapely BSD, pyclipper MIT), but on 2026-11-12: the
  models bundled in the wheel are the Chinese/English PP-OCRv4 set, which do not cover
  Spanish accents and «Ñ»; the Latin recognition model would have to be vendored into the
  image (a runtime download is not allowed in-cluster); and `opencv-python` needs
  `libGL` in the slim image. The csf lane's synthetic accuracy with prepared pages and
  Tesseract (README) did not need it. Revisit with a vendored Latin model when photo
  accuracy on real uploads says so.

## What production still needs (owner steps)

From README «Launching the api and worker», sync-only path (enough for the csf and
letterhead lanes, which run in sync mode):

1. Enclii postgres addon → Secret `tlacuilo-secrets` (`DATABASE_URL`) in namespace `tlacuilo`,
   and the pooler entry for the new role (until then `/ready` fails, by design).
2. Janua service clients for each caller with the role/scope `tlacuilo:extract` and the
   audience `tlacuilo` — for Karafiel's CSF intake: `karafiel-tlacuilo-ocr`.
3. The launch PR: uncomment `api-deployment.yaml` in `infra/k8s/production/kustomization.yaml`
   and set `API_LAUNCHED = True` in `tests/test_manifests.py`.
4. Consumers open their own egress to `tlacuilo:80` (Karafiel's ships with its CSF OCR PR);
   `allow-consumer-ingress` already admits `karafiel` and `enclii-karafiel-staging`.

The worker, queue, gateway token and callback key (full path) are not needed by the csf lane.
