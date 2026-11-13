# syntax=docker/dockerfile:1.7
# tlacuilo — API and worker share this image (the worker runs `celery` from it).
# CPU-only by design (RFC 0040 M0–M2). Tesseract + Spanish data (`spa`) and the
# orientation data (`osd`, a dependency of the package) are called by the letterhead
# lane (tlacuilo/engines/ocr.py) and the csf lane (tlacuilo/engines/scan.py); QR codes
# (zxing-cpp) and HEIC photos (pi-heif) need no system package. M1's statement scan
# lane will need no image change.
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 1001 tlacuilo && useradd -u 1001 -g 1001 -m -s /usr/sbin/nologin tlacuilo
WORKDIR /app
COPY pyproject.toml README.md ./
COPY tlacuilo ./tlacuilo
COPY contracts ./contracts
RUN pip install --no-cache-dir .
USER 1001
EXPOSE 8000
# Bytes live in memory and /tmp (an emptyDir in the Deployment) for one job only.
CMD ["uvicorn", "tlacuilo.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
