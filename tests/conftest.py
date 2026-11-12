import datetime as dt
import os
import shutil
import subprocess

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

os.environ.setdefault("TLACUILO_ENV", "test")
os.environ.setdefault("JANUA_BASE_URL", "https://auth.test.madfam.io")
os.environ.setdefault("JANUA_AUDIENCE", "tlacuilo")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("CELERY_BROKER_URL", "")
os.environ.setdefault("CELERY_RESULT_BACKEND", "")

_PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_PEM = _PRIVATE.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)


def _spanish_ocr_installed() -> bool:
    binary = shutil.which("tesseract")
    if binary is None:
        return False
    try:
        listed = subprocess.run([binary, "--list-langs"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return b"spa" in (listed.stdout + listed.stderr).split()


# The image ships Tesseract with Spanish data and CI installs the same packages, so the
# OCR path runs there; a bare machine skips these tests instead of failing them.
needs_ocr_engine = pytest.mark.skipif(
    not _spanish_ocr_installed(), reason="tesseract with Spanish language data is not installed"
)


@pytest.fixture(autouse=True)
def _janua_keys(monkeypatch):
    import tlacuilo.auth as auth

    monkeypatch.setattr(auth, "signing_key_for", lambda token: _PUBLIC_PEM)
    yield


def mint(roles=("tlacuilo:extract",), org="org-test", aud="tlacuilo", iss="https://auth.test.madfam.io", expired=False):
    now = dt.datetime.now(dt.UTC)
    claims = {
        "sub": "svc-dhanam",
        "iss": iss,
        "aud": aud,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=-5 if expired else 5)).timestamp()),
        "org_id": org,
        "roles": list(roles),
    }
    return jwt.encode(claims, _PRIVATE, algorithm="RS256", headers={"kid": "test"})


@pytest.fixture
def token():
    return mint()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from tlacuilo.api import app

    with TestClient(app) as c:
        yield c
