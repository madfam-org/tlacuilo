from prometheus_client import Counter, Histogram

EXTRACTIONS = Counter("tlacuilo_extractions_total", "Extractions by outcome", ["doc_type", "outcome"])
PAGES = Counter("tlacuilo_pages_total", "Pages processed", ["engine"])
DURATION = Histogram(
    "tlacuilo_extraction_seconds", "Wall time per extraction", buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60)
)
VALIDATION = Counter("tlacuilo_validation_total", "Statement validation outcomes", ["result"])
