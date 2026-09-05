"""HTTP surface: /health, /ready, /metrics, POST /v1/extractions, GET /v1/extractions/{id}.

Sync mode takes a multipart upload (≤ SYNC_MAX_PAGES) and returns the result in the
response — nothing is kept. Async mode takes the caller's presigned object URL, runs on
the worker and hands the result back once (callback and/or a single GET)."""

from __future__ import annotations

import datetime as dt
import logging
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import __version__, db
from .auth import Principal, require_principal
from .contract import DocumentType, Sensitivity
from .logging import configure_logging
from .metrics import EXTRACTIONS
from .pipeline import ExtractionError, engines_available, run_extraction
from .settings import get_settings

log = logging.getLogger(__name__)
app = FastAPI(title="tlacuilo", version=__version__, docs_url="/docs", redoc_url=None)


@app.on_event("startup")
async def _startup() -> None:
    configure_logging()
    db.init_db()


def require_sensitivity(request: Request) -> Sensitivity:
    raw = request.headers.get("x-sensitivity")
    if raw is None or not raw.strip():
        raise HTTPException(
            status_code=400, detail={"error": "missing_sensitivity", "allowed": [s.value for s in Sensitivity]}
        )
    try:
        return Sensitivity(raw.strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"error": "invalid_sensitivity", "allowed": [s.value for s in Sensitivity]}
        ) from None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "tlacuilo", "version": __version__}


@app.get("/ready")
async def ready() -> JSONResponse:
    s = get_settings()
    checks: dict[str, str] = {}
    ok = True
    try:
        db.ping()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error:{type(exc).__name__}"
        ok = False
    if s.celery_result_backend:
        try:
            import redis

            redis.Redis.from_url(s.celery_result_backend, socket_connect_timeout=2).ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error:{type(exc).__name__}"
            ok = False
    else:
        checks["redis"] = "unconfigured"
    checks["engines"] = engines_available()  # type: ignore[assignment]
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _host_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host.endswith(suffix) for suffix in get_settings().fetch_host_suffixes)


@app.post("/v1/extractions")
async def create_extraction(
    principal: Principal = Depends(require_principal),
    sensitivity: Sensitivity = Depends(require_sensitivity),
    file: UploadFile | None = File(default=None),
    mode: str = Form(default="sync"),
    document_type: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    callback_url: str | None = Form(default=None),
):
    s = get_settings()
    hint = None
    if document_type:
        try:
            hint = DocumentType(document_type)
        except ValueError:
            raise HTTPException(status_code=400, detail={"error": "invalid_document_type"}) from None

    if mode == "sync":
        if file is None:
            raise HTTPException(status_code=400, detail={"error": "file_required"})
        data = await file.read(s.max_upload_bytes + 1)
        if len(data) > s.max_upload_bytes:
            raise HTTPException(status_code=413, detail={"error": "too_large", "limit": s.max_upload_bytes})
        if not data:
            raise HTTPException(status_code=400, detail={"error": "empty_file"})
        with db.session() as session:
            db.purge_expired(session)
            job = db.new_job(
                session,
                org_id=principal.org_id,
                status="running",
                mode="sync",
                sensitivity=sensitivity.value,
                size_bytes=len(data),
            )
            try:
                result = run_extraction(
                    data, file.content_type or "", sensitivity, max_pages=s.sync_max_pages, doc_type_hint=hint
                )
            except ExtractionError as exc:
                job.status, job.error_code = "failed", exc.code
                session.commit()
                EXTRACTIONS.labels(doc_type=(hint.value if hint else "unknown"), outcome="error").inc()
                return JSONResponse({"error": exc.code, "job": job.id, **exc.extra}, status_code=exc.status)
            job.status = "done"
            job.doc_type = result.document.type.value
            job.sha256 = result.document.sha256
            job.pages = result.document.pages
            job.engines = result.provenance.engines.model_dump()
            job.duration_ms = result.provenance.durationMs
            session.commit()
            return JSONResponse(result.model_dump(mode="json"), headers={"X-Tlacuilo-Job": job.id})

    if mode == "async":
        if not source_url or not _host_allowed(source_url):
            raise HTTPException(
                status_code=400,
                detail={"error": "source_url_required", "allowed_suffixes": list(s.fetch_host_suffixes)},
            )
        if callback_url and not callback_url.startswith("https://"):
            raise HTTPException(status_code=400, detail={"error": "callback_url_must_be_https"})
        if not s.celery_broker_url:
            raise HTTPException(status_code=503, detail={"error": "queue_unconfigured"})
        from .worker import extract_from_url

        with db.session() as session:
            db.purge_expired(session)
            job = db.new_job(
                session,
                org_id=principal.org_id,
                status="queued",
                mode="async",
                sensitivity=sensitivity.value,
                doc_type=hint.value if hint else None,
            )
        extract_from_url.delay(job.id, source_url, sensitivity.value, hint.value if hint else None, callback_url)
        return JSONResponse({"id": job.id, "status": "queued"}, status_code=202)

    raise HTTPException(status_code=400, detail={"error": "invalid_mode", "allowed": ["sync", "async"]})


@app.get("/v1/extractions/{job_id}")
async def get_extraction(job_id: str, principal: Principal = Depends(require_principal)) -> JSONResponse:
    with db.session() as session:
        job = session.get(db.Job, job_id)
        if job is None or job.org_id != principal.org_id:
            raise HTTPException(status_code=404, detail={"error": "not_found"})
        payload = job.public()
        if job.mode == "async" and job.status == "done":
            from .worker import take_result

            result = take_result(job.id)
            payload["result"] = result  # None once it has been delivered (one-shot)
            payload["resultDelivered"] = result is None
        payload["now"] = dt.datetime.now(dt.UTC).isoformat()
        return JSONResponse(payload)
