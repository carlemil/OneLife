"""Pluggable image generation for location art (ATMOSPHERE.md / MEDIA_PROVIDERS.md).

Anthropic has no image API, so this calls a configurable OpenAI-compatible images
endpoint (OpenAI, or anything exposing POST /images/generations) when
IMAGE_API_KEY is set. Unconfigured → returns None and the caller uses the
deterministic procedural SVG instead. Generated images are cached per theme.
"""
import os

import httpx

API_KEY = os.environ.get("IMAGE_API_KEY", "").strip()
API_BASE = os.environ.get("IMAGE_API_BASE", "https://api.openai.com/v1").rstrip("/")
MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-1")
SIZE = os.environ.get("IMAGE_SIZE", "1024x1024")
CONFIGURED = bool(API_KEY)


async def generate(prompt: str) -> str | None:
    """Return an image URL or data URI, or None on any failure."""
    if not CONFIGURED:
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{API_BASE}/images/generations",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={"model": MODEL, "prompt": prompt, "n": 1, "size": SIZE})
        r.raise_for_status()
        d = r.json()["data"][0]
        if d.get("b64_json"):
            return "data:image/png;base64," + d["b64_json"]
        return d.get("url")
    except Exception:  # noqa: BLE001 — image gen is best-effort; fall back to SVG
        return None
