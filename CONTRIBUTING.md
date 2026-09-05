# Contributing

Thank you for reading a codex scribe's code. tlacuilo is AGPL-3.0-only: if you run a
modified version as a network service, you owe your users the source. Contributions are
accepted under the same licence.

- Read [AGENTS.md](./AGENTS.md) first — it states the four doctrines a change may not break.
- A parser ships with fixtures and a measured number (`tests/synth.py` is the harness seed);
  a parser without a number is a draft.
- Never add a dependency that talks to a hosted OCR or model API, and never add a provider
  key. `scripts/check-selva-contract.sh` will refuse it anyway.
- Conventional commits; keep PRs small; CI must be green (lint, tests, contract sync,
  licence gate, secret coverage, manifest rules, image build).
