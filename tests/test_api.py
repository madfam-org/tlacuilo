from decimal import Decimal

from .conftest import mint
from .synth import make_statement


def _post(client, token, pdf, **extra):
    headers = {"Authorization": f"Bearer {token}", "X-Sensitivity": extra.pop("sensitivity", "restricted")}
    return client.post(
        "/v1/extractions", headers=headers, files={"file": ("st.pdf", pdf, "application/pdf")}, data=extra
    )


def test_health_and_ready(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["db"] == "ok"
    assert client.get("/metrics").status_code == 200


def test_requires_bearer_and_role(client, token):
    pdf, _ = make_statement()
    r = client.post(
        "/v1/extractions", headers={"X-Sensitivity": "restricted"}, files={"file": ("st.pdf", pdf, "application/pdf")}
    )
    assert r.status_code == 401 and r.json()["detail"]["error"] == "missing_bearer"
    r = _post(client, mint(roles=("other:role",)), pdf)
    assert r.status_code == 403
    r = _post(client, mint(expired=True), pdf)
    assert r.status_code == 401 and r.json()["detail"]["reason"] == "ExpiredSignatureError"
    r = _post(client, mint(aud="someone-else"), pdf)
    assert r.status_code == 401


def test_sensitivity_is_required_and_closed(client, token):
    pdf, _ = make_statement()
    r = client.post(
        "/v1/extractions",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("st.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 400 and r.json()["detail"]["error"] == "missing_sensitivity"
    r = _post(client, token, pdf, sensitivity="low")
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_sensitivity"


def test_sync_extraction_roundtrip(client, token):
    pdf, truth = make_statement()
    r = _post(client, token, pdf)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contract"] == "document-extraction/v1"
    assert body["statement"]["bank"] == "BBVA"
    assert Decimal(str(body["statement"]["closingBalance"])) == truth.closing
    assert body["validation"]["balanceReconciles"] is True
    job_id = r.headers["X-Tlacuilo-Job"]
    j = client.get(f"/v1/extractions/{job_id}", headers={"Authorization": f"Bearer {token}"})
    assert j.status_code == 200
    assert j.json()["status"] == "done" and j.json()["pages"] == 1 and "result" not in j.json()
    other = client.get(f"/v1/extractions/{job_id}", headers={"Authorization": f"Bearer {mint(org='org-other')}"})
    assert other.status_code == 404


def test_image_needs_ocr_and_bad_mime(client, token):
    r = client.post(
        "/v1/extractions",
        headers={"Authorization": f"Bearer {token}", "X-Sensitivity": "restricted"},
        files={"file": ("scan.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")},
    )
    assert r.status_code == 422 and r.json()["error"] == "needs_ocr"
    r = client.post(
        "/v1/extractions",
        headers={"Authorization": f"Bearer {token}", "X-Sensitivity": "restricted"},
        files={"file": ("x.bin", b"hello", "application/octet-stream")},
    )
    assert r.status_code == 422 and r.json()["error"] == "unsupported_mime"


def test_async_requires_allowlisted_https_source(client, token):
    r = client.post(
        "/v1/extractions",
        headers={"Authorization": f"Bearer {token}", "X-Sensitivity": "restricted"},
        data={"mode": "async", "source_url": "http://evil.example/x.pdf"},
    )
    assert r.status_code == 400 and r.json()["detail"]["error"] == "source_url_required"
    r = client.post(
        "/v1/extractions",
        headers={"Authorization": f"Bearer {token}", "X-Sensitivity": "restricted"},
        data={"mode": "async", "source_url": "https://acct.r2.cloudflarestorage.com/bucket/key?sig=1"},
    )
    assert r.status_code == 503 and r.json()["detail"]["error"] == "queue_unconfigured"


def test_the_api_serves_sync_mode_with_the_database_alone(client, token, monkeypatch):
    # The sync-only launch: no queue URLs, no gateway token, no callback key in the
    # environment. The api is ready, sync extractions work, async says why it cannot.
    from tlacuilo.settings import get_settings, reset_settings_cache

    for key in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND", "SELVA_API_KEY", "CALLBACK_HMAC_KEY"):
        monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    try:
        s = get_settings()
        assert (s.celery_broker_url, s.celery_result_backend, s.selva_api_key, s.callback_hmac_key) == ("", "", "", "")
        r = client.get("/ready")
        assert r.status_code == 200 and r.json()["ready"] is True
        assert r.json()["checks"]["db"] == "ok" and r.json()["checks"]["redis"] == "unconfigured"
        pdf, _ = make_statement()
        r = _post(client, token, pdf)
        assert r.status_code == 200 and r.json()["validation"]["balanceReconciles"] is True
        job = client.get(f"/v1/extractions/{r.headers['X-Tlacuilo-Job']}", headers={"Authorization": f"Bearer {token}"})
        assert job.status_code == 200 and job.json()["status"] == "done"
        r = client.post(
            "/v1/extractions",
            headers={"Authorization": f"Bearer {token}", "X-Sensitivity": "restricted"},
            data={"mode": "async", "source_url": "https://acct.r2.cloudflarestorage.com/bucket/key?sig=1"},
        )
        assert r.status_code == 503 and r.json()["detail"]["error"] == "queue_unconfigured"
    finally:
        reset_settings_cache()
