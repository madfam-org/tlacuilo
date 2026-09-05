# Security

tlacuilo processes financial and identity documents on behalf of other MADFAM services.
Please report vulnerabilities privately to **security@madfam.io** — not in a public issue.
We acknowledge reports within three business days.

Scope reminders for reviewers:

- The API is not reachable from the internet; only the landing page is. Findings about
  the API's exposure are welcome, but reproduce them from inside a consumer's position.
- The service keeps no document content. A finding that shows content being persisted or
  logged is the highest severity we recognise.
- Every model call must go through the ecosystem's inference gateway with a sensitivity
  label; a path that bypasses it is a vulnerability, not a feature.
