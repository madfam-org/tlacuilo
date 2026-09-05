"""Celery worker for async mode. The broker is the shared Redis (AUTH, own DB index); the
one-shot result store is the same Redis with RESULT_TTL_SECONDS — the M0 compromise
recorded in AGENTS.md (Redis may persist to disk briefly; the row store never sees
content). Bytes are fetched from the caller's presigned URL, never stored."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx
from celery import Celery

from . import db
from .contract import DocumentType, Sensitivity
from .pipeline import ExtractionError, run_extraction
from .settings import get_settings

log = logging.getLogger(__name__)
_settings = get_settings()
celery_app = Celery("tlacuilo", broker=_settings.celery_broker_url or "memory://")
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,  # results never go through Celery's backend
    broker_connection_retry_on_startup=True,
)


def _redis():
    import redis

    return redis.Redis.from_url(_settings.celery_result_backend)


def result_key(job_id: str) -> str:
    return f"tlacuilo:result:{job_id}"


def store_result(job_id: str, payload: dict) -> None:
    _redis().set(result_key(job_id), json.dumps(payload), ex=_settings.result_ttl_seconds)


def take_result(job_id: str) -> dict | None:
    raw = _redis().getdel(result_key(job_id))
    return json.loads(raw) if raw else None


def sign(body: bytes, key: str) -> str:
    return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def _fetch(url: str) -> tuple[bytes, str]:
    limit = _settings.max_upload_bytes
    with httpx.Client(timeout=30, follow_redirects=False) as client, client.stream("GET", url) as resp:
        resp.raise_for_status()
        chunks, total = [], 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > limit:
                raise ExtractionError("too_large", status=413, limit=limit)
            chunks.append(chunk)
        return b"".join(chunks), resp.headers.get("content-type", "application/pdf")


@celery_app.task(name="tlacuilo.extract_from_url")
def extract_from_url(job_id: str, url: str, sensitivity: str, doc_type: str | None, callback_url: str | None) -> None:
    with db.session() as session:
        job = session.get(db.Job, job_id)
        if job is None:
            log.warning("job %s vanished before the worker ran", job_id)
            return
        job.status = "running"
        session.commit()
        payload: dict
        try:
            data, mime = _fetch(url)
            job.size_bytes = len(data)
            result = run_extraction(
                data, mime, Sensitivity(sensitivity), doc_type_hint=DocumentType(doc_type) if doc_type else None
            )
            payload = result.model_dump(mode="json")
            job.status, job.doc_type, job.sha256 = "done", result.document.type.value, result.document.sha256
            job.pages, job.engines, job.duration_ms = (
                result.document.pages,
                result.provenance.engines.model_dump(),
                result.provenance.durationMs,
            )
            store_result(job_id, payload)
        except ExtractionError as exc:
            job.status, job.error_code = "failed", exc.code
            payload = {"id": job_id, "status": "failed", "error": exc.code}
        except Exception as exc:  # network, corrupt input — fail closed, name the class only
            job.status, job.error_code = "failed", f"exception:{type(exc).__name__}"
            payload = {"id": job_id, "status": "failed", "error": job.error_code}
        session.commit()
    if callback_url:
        body = json.dumps(
            {
                "id": job_id,
                "status": payload.get("status", "done"),
                "result": payload if "contract" in payload else None,
            }
        ).encode()
        headers = {"Content-Type": "application/json", "X-Tlacuilo-Job": job_id}
        if _settings.callback_hmac_key:
            headers["X-Tlacuilo-Signature"] = sign(body, _settings.callback_hmac_key)
        try:
            httpx.post(callback_url, content=body, headers=headers, timeout=15)
        except httpx.HTTPError as exc:
            log.warning("callback for job %s failed: %s", job_id, type(exc).__name__)
