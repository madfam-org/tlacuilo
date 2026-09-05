"""tlacuilo — MADFAM document-intelligence service (RFC 0040).

OCR + typed extraction that keeps nothing durable: bytes live in memory for one job,
results carry per-field confidence and provenance, and every model call goes through
Selva with the caller's sensitivity label. See AGENTS.md for the four doctrines.
"""

__version__ = "0.1.0"
