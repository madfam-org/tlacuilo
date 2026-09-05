#!/usr/bin/env bash
# The Selva contract, enforced (RFC 0034 / ECOSYSTEM.md): every model call goes
# through Selva /v1; this service holds no provider key and speaks to no hosted
# OCR or document-AI API. Pattern borrowed from avala/scripts/check-selva-ai-contract.mjs.
set -euo pipefail
cd "$(dirname "$0")/.."
PATTERN='api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis|OPENAI_API_KEY|ANTHROPIC_API_KEY|GOOGLE_API_KEY|AZURE_OPENAI|textract|documentai|formrecognizer|mindee|from openai|import openai|import anthropic|from anthropic|google\.cloud\.vision|boto3'
if grep -rnE --include='*.py' --include='*.toml' --include='*.yaml' --include='*.yml' "$PATTERN" tlacuilo pyproject.toml infra enclii.yaml; then
  echo "selva-contract: forbidden provider/OCR-cloud reference found (see lines above)" >&2
  exit 1
fi
echo "selva-contract: clean (no provider SDKs, keys, or hosted OCR APIs referenced)"
