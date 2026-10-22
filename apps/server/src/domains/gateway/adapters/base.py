"""Generic async HTTP video/image adapter. Vendor specifics live in per-provider ProviderConfig.

NOT_TESTED against any live vendor: paths, field names, auth and callback shape are configurable because
resellers (ApiPass, WaveSpeedAI, Segmind, fal, ...) expose the same model under different contracts.
"""
import hashlib
import hmac
import os
from dataclasses import dataclass, field
from typing import Any

import httpx


class ProviderError(Exception):
    pass


class ProviderRetryable(ProviderError):
    """5xx or timeout: refund and fail over."""

    def __init__(self, msg: str, timeout: bool = False):
        super().__init__(msg)
        self.timeout = timeout


class ProviderRejected(ProviderError):
    """4xx: refund, no failover (the same payload would be rejected elsewhere too)."""


class ProviderNotConfigured(ProviderError):
    pass


@dataclass(frozen=True)
class CallbackResult:
    provider_job_id: str
    ok: bool
    video_url: str | None = None
    last_frame_url: str | None = None
    billed_seconds: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    create_paths: dict[str, str]                 # effective task_type -> path
    model_ids: dict[str, str] = field(default_factory=dict)   # model_key -> vendor model id
    field_map: dict[str, str | None] = field(default_factory=dict)  # canonical -> vendor name (None drops)
    job_id_key: str = "task_id"
    signature_header: str = "X-Webhook-Signature"
    cb_job_id: str = "task_id"
    cb_status: str = "status"
    cb_ok: frozenset[str] = frozenset({"succeeded", "success", "completed"})
    cb_video_url: str = "video_url"
    cb_last_frame_url: str = "last_frame_url"
    cb_seconds: str = "duration"
    cb_error: str = "error"
    timeout_s: float = 60.0

    @property
    def env_prefix(self) -> str:
        return self.name.upper()


class HttpAdapter:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def build_body(self, model_key: str, canonical: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.cfg.model_ids.get(model_key, model_key)}
        for k, v in canonical.items():
            name = self.cfg.field_map.get(k, k)
            if name is not None:
                body[name] = v
        return body

    async def submit(self, client: httpx.AsyncClient, *, api_base: str, model_key: str, task_type: str,
                     canonical: dict[str, Any]) -> str:
        key = os.environ.get(f"{self.cfg.env_prefix}_API_KEY")
        if api_base.startswith("unconfigured://") or not key:
            raise ProviderNotConfigured(f"{self.cfg.name}: set model_catalog.api_base and {self.cfg.env_prefix}_API_KEY")
        path = self.cfg.create_paths.get(task_type)
        if path is None:
            raise ProviderRejected(f"{self.cfg.name} has no endpoint for {task_type}")
        try:
            r = await client.post(api_base.rstrip("/") + path, json=self.build_body(model_key, canonical),
                                  headers={"Authorization": f"Bearer {key}"}, timeout=self.cfg.timeout_s)
        except httpx.TimeoutException as e:
            raise ProviderRetryable(f"{self.cfg.name} timeout: {e}", timeout=True) from e
        except httpx.TransportError as e:
            raise ProviderRetryable(f"{self.cfg.name} transport error: {e}") from e
        if r.status_code >= 500:
            raise ProviderRetryable(f"{self.cfg.name} HTTP {r.status_code}")
        if r.status_code >= 400:
            raise ProviderRejected(f"{self.cfg.name} HTTP {r.status_code}: {r.text[:300]}")
        return str(r.json()[self.cfg.job_id_key])

    def verify_signature(self, raw: bytes, signature: str | None) -> bool:
        secret = os.environ.get(f"{self.cfg.env_prefix}_WEBHOOK_SECRET")
        if not secret or not signature:
            return False  # fail closed
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.strip())

    def parse_callback(self, data: dict[str, Any]) -> CallbackResult:
        c = self.cfg
        secs = data.get(c.cb_seconds)
        return CallbackResult(
            provider_job_id=str(data[c.cb_job_id]), ok=str(data.get(c.cb_status, "")).lower() in c.cb_ok,
            video_url=data.get(c.cb_video_url), last_frame_url=data.get(c.cb_last_frame_url),
            billed_seconds=int(secs) if secs is not None else None, error=data.get(c.cb_error))
