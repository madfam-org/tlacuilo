"""Runtime configuration. Every key here is read from the environment; the CI check
`scripts/check-secret-coverage.py` fails if a key is read by code but declared in no
production manifest (source "env" → a plain env var, "secret" → an ExternalSecret data
key, "addon" → written by the Enclii Postgres addon, "code" → never comes from the
cluster: tests and derived values only)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    tlacuilo_env: str = Field("production", json_schema_extra={"source": "env"})

    # Identity: Janua RS256 only. Tokens are service-client JWTs carrying the role.
    janua_base_url: str = Field("https://auth.madfam.io", json_schema_extra={"source": "env"})
    janua_audience: str = Field("tlacuilo", json_schema_extra={"source": "env"})
    required_role: str = Field("tlacuilo:extract", json_schema_extra={"source": "code"})

    # Inference: Selva is the only path a document pixel may take to a model (M3).
    selva_base_url: str = Field("http://inference-gateway.selva.svc.cluster.local", json_schema_extra={"source": "env"})
    selva_api_key: str = Field("", json_schema_extra={"source": "secret"})
    selva_enabled: bool = Field(False, json_schema_extra={"source": "env"})

    # Job metadata store (ids, hashes, sizes, timings — never content).
    database_url: str = Field("", json_schema_extra={"source": "addon"})

    # Queue + the short-lived one-shot result store (async mode only).
    celery_broker_url: str = Field("", json_schema_extra={"source": "secret"})
    celery_result_backend: str = Field("", json_schema_extra={"source": "secret"})

    # Callback signing (async mode). Per-client keys arrive in M1.
    callback_hmac_key: str = Field("", json_schema_extra={"source": "secret"})

    # Async mode fetches the caller's presigned object URL; only these host suffixes.
    fetch_host_allowlist: str = Field(".r2.cloudflarestorage.com", json_schema_extra={"source": "env"})

    max_upload_bytes: int = Field(25 * 1024 * 1024, json_schema_extra={"source": "env"})
    sync_max_pages: int = Field(10, json_schema_extra={"source": "env"})
    job_ttl_seconds: int = Field(86400, json_schema_extra={"source": "env"})
    result_ttl_seconds: int = Field(900, json_schema_extra={"source": "env"})

    # Tests only. Refused outside test environments (see validate_runtime).
    auth_disabled: bool = Field(False, json_schema_extra={"source": "code"})

    def validate_runtime(self) -> None:
        if self.auth_disabled and self.tlacuilo_env not in {"test", "local"}:
            raise RuntimeError("AUTH_DISABLED is only honoured when TLACUILO_ENV is test or local")

    @property
    def fetch_host_suffixes(self) -> tuple[str, ...]:
        return tuple(s.strip().lower() for s in self.fetch_host_allowlist.split(",") if s.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.validate_runtime()
    return s


def reset_settings_cache() -> None:
    get_settings.cache_clear()
