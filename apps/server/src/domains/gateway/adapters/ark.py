"""Volcengine Ark (火山方舟): Seedance 2.5 video tasks + Seedream 5.0 images.

Request/response shapes follow the official Ark API reference (read 2026-10-22):
  POST {base}/contents/generations/tasks       create video task -> {"id"}
  GET  {base}/contents/generations/tasks/{id}  status queued|running|succeeded|failed|expired|cancelled,
                                               content.video_url / content.last_frame_url, duration, error
  POST {base}/images/generations               synchronous -> data[].url
Ark callbacks are unsigned, so we poll instead of trusting callback_url. NOT_TESTED live: no ARK_API_KEY here.
"""
import os
from typing import Any

import httpx

from src import media
from src.domains.gateway.adapters.base import (
    CallbackResult,
    ProviderNotConfigured,
    ProviderRejected,
    ProviderRetryable,
)

# model_key -> (default vendor model id, extra body). Override the id with ARK_MODEL_<MODEL_KEY> (e.g. an
# Endpoint ID) when your account uses a different version.
MODELS: dict[str, tuple[str, dict[str, Any]]] = {
    "seedance_2_5_spicy": ("doubao-seedance-2-5-260628", {}),
    "seedance_2_5_draft": ("doubao-seedance-2-5-260628", {"draft": True}),  # 样片模式: 480p only
    "seedream_5_pro": ("doubao-seedream-5-0-pro-260628", {}),
}
DONE = {"succeeded", "failed", "expired", "cancelled"}


class ArkAdapter:
    name = "ark"

    def model_id(self, model_key: str) -> str:
        default = MODELS.get(model_key, (model_key, {}))[0]
        return os.environ.get(f"ARK_MODEL_{model_key.upper()}", default)

    def _key(self, api_base: str) -> str:
        key = os.environ.get("ARK_API_KEY")
        if api_base.startswith("unconfigured://") or not key:
            raise ProviderNotConfigured("ark: set ARK_API_KEY (火山方舟控制台 → API Key 管理)")
        return key

    def video_body(self, model_key: str, task_type: str, c: dict[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": c["prompt"]}]
        if c.get("first_frame_url"):
            content.append({"type": "image_url", "image_url": {"url": media.data_url(c["first_frame_url"])},
                            "role": "first_frame"})
            if c.get("last_frame_url"):
                content.append({"type": "image_url", "image_url": {"url": media.data_url(c["last_frame_url"])},
                                "role": "last_frame"})
        for u in c.get("reference_image_urls") or []:
            content.append({"type": "image_url", "image_url": {"url": media.data_url(u)}, "role": "reference_image"})
        for u in c.get("reference_video_urls") or []:  # videos must be public URLs (no base64 for video)
            content.append({"type": "video_url", "video_url": {"url": u}, "role": "reference_video"})
        for u in c.get("reference_audio_urls") or []:
            content.append({"type": "audio_url", "audio_url": {"url": media.data_url(u)}, "role": "reference_audio"})
        # first-frame mode and edit/extend only accept ratio=adaptive on Seedance 2.5
        ratio = "adaptive" if c.get("first_frame_url") or task_type in ("video.edit", "video.extend") \
            else c.get("aspect_ratio", "adaptive")
        body: dict[str, Any] = {"model": self.model_id(model_key), "content": content, "ratio": ratio,
                                "duration": c.get("duration", 5), "resolution": c.get("resolution", "720p"),
                                "generate_audio": c.get("generate_audio", True),
                                "return_last_frame": c.get("return_last_frame", False), "watermark": False}
        if c.get("omni_reference_task_type"):
            body["omni_reference_task_type"] = c["omni_reference_task_type"]
        body |= MODELS.get(model_key, ("", {}))[1]
        if body.get("draft"):
            body["resolution"] = "480p"
        return body

    def image_body(self, model_key: str, c: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model_id(model_key), "prompt": c["prompt"], "size": "2K",
                                "response_format": "url", "watermark": False}
        refs = c.get("reference_image_urls") or []
        if refs:
            body["image"] = [media.data_url(u) for u in refs]
        return body

    async def _call(self, client: httpx.AsyncClient, method: str, url: str, key: str,
                    body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            r = await client.request(method, url, json=body, headers={"Authorization": f"Bearer {key}"},
                                     timeout=180)
        except httpx.TimeoutException as e:
            raise ProviderRetryable(f"ark timeout: {e}", timeout=True) from e
        except httpx.TransportError as e:
            raise ProviderRetryable(f"ark transport error: {e}") from e
        if r.status_code >= 500 or r.status_code == 429:
            raise ProviderRetryable(f"ark HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code >= 400:
            raise ProviderRejected(f"ark HTTP {r.status_code}: {r.text[:300]}")
        data: dict[str, Any] = r.json()
        return data

    async def submit(self, client: httpx.AsyncClient, *, api_base: str, model_key: str, task_type: str,
                     canonical: dict[str, Any]) -> str | CallbackResult:
        key, base = self._key(api_base), api_base.rstrip("/")
        if task_type.startswith("image."):
            data = await self._call(client, "POST", f"{base}/images/generations", key,
                                    self.image_body(model_key, canonical))
            urls = [d["url"] for d in data.get("data", []) if d.get("url")]
            if not urls:
                raise ProviderRejected(f"ark image: no image returned {str(data)[:300]}")
            return CallbackResult(provider_job_id=f"img-{data.get('created', '')}", ok=True, video_url=urls[0])
        data = await self._call(client, "POST", f"{base}/contents/generations/tasks", key,
                                self.video_body(model_key, task_type, canonical))
        return str(data["id"])

    async def poll(self, client: httpx.AsyncClient, *, api_base: str, provider_job_id: str) -> CallbackResult | None:
        data = await self._call(client, "GET", f"{api_base.rstrip('/')}/contents/generations/tasks/{provider_job_id}",
                                self._key(api_base))
        status = str(data.get("status", ""))
        if status not in DONE:
            return None
        content, err = data.get("content") or {}, data.get("error") or {}
        dur = data.get("duration")
        return CallbackResult(provider_job_id=provider_job_id, ok=status == "succeeded",
                              video_url=content.get("video_url"), last_frame_url=content.get("last_frame_url"),
                              billed_seconds=int(dur) if isinstance(dur, int | float) and dur > 0 else None,
                              error=None if status == "succeeded" else f"{status}: {err.get('code')} {err.get('message')}")

    def verify_signature(self, raw: bytes, signature: str | None) -> bool:
        return False  # Ark does not sign callbacks; results come from poll()

    def parse_callback(self, data: dict[str, Any]) -> CallbackResult:
        raise NotImplementedError
