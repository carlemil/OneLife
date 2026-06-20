"""Pluggable image generation for location art (ATMOSPHERE.md / MEDIA_PROVIDERS.md).

Two providers, selected by IMAGE_PROVIDER:
  * "openai" (default) — an OpenAI-compatible POST /images/generations endpoint
    (OpenAI, or anything compatible); needs IMAGE_API_KEY. Anthropic has no image API.
  * "pollinations" — the free Pollinations endpoint (https://image.pollinations.ai).
    Text-to-image (model "flux") is keyless: `generate` returns a deterministic URL
    that renders on first fetch. If POLLINATIONS_TOKEN is set AND a reference photo
    is supplied, it instead does authenticated image-to-image (model "kontext"):
    the real photo of the actual location is restyled into our dark mood, fetched
    server-side (token stays server-side) and returned as a data: URI.

Unconfigured → returns None and the caller uses the deterministic procedural SVG.
Generated images are cached per theme in `generated_images`.
"""
import os
import base64
import hashlib
import urllib.parse

import httpx

PROVIDER = os.environ.get("IMAGE_PROVIDER", "openai").strip().lower()

# OpenAI-compatible provider
API_KEY = os.environ.get("IMAGE_API_KEY", "").strip()
API_BASE = os.environ.get("IMAGE_API_BASE", "https://api.openai.com/v1").rstrip("/")
MODEL = os.environ.get("IMAGE_MODEL", "gpt-image-1")
SIZE = os.environ.get("IMAGE_SIZE", "1024x1024")

# Pollinations provider
POLLINATIONS_BASE = os.environ.get("POLLINATIONS_BASE", "https://image.pollinations.ai").rstrip("/")
POLLINATIONS_MODEL = os.environ.get("POLLINATIONS_MODEL", "flux")
POLLINATIONS_EDIT_MODEL = os.environ.get("POLLINATIONS_EDIT_MODEL", "kontext")
POLLINATIONS_TOKEN = os.environ.get("POLLINATIONS_TOKEN", "").strip()
_BW, _BH = (int(x) for x in os.environ.get("IMAGE_BANNER_SIZE", "1024x384").split("x"))

CONFIGURED = PROVIDER == "pollinations" or bool(API_KEY)
# image-to-image (real-photo grounding) needs an authenticated token
I2I_ENABLED = PROVIDER == "pollinations" and bool(POLLINATIONS_TOKEN)


def _seed(s: str) -> int:
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % 1_000_000


def _pollinations_url(prompt: str) -> str:
    """Deterministic keyless text-to-image URL — a fixed seed per prompt keeps a
    theme's image stable across page loads (Pollinations caches by prompt+seed)."""
    q = urllib.parse.quote(prompt, safe="")
    return (f"{POLLINATIONS_BASE}/prompt/{q}"
            f"?width={_BW}&height={_BH}&seed={_seed(prompt)}&nologo=true&model={POLLINATIONS_MODEL}")


async def _pollinations_i2i(prompt: str, reference: str) -> str | None:
    """Authenticated image-to-image: restyle the real reference photo per `prompt`.
    Fetched server-side (token never leaves the server) and returned as a data: URI.
    Returns None on any failure so the caller can fall back to text-to-image."""
    q = urllib.parse.quote(prompt, safe="")
    r = urllib.parse.quote(reference, safe="")
    url = (f"{POLLINATIONS_BASE}/prompt/{q}"
           f"?model={POLLINATIONS_EDIT_MODEL}&image={r}"
           f"&width={_BW}&height={_BH}&seed={_seed(prompt + reference)}&nologo=true")
    try:
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
            resp = await c.get(url, headers={"Authorization": f"Bearer {POLLINATIONS_TOKEN}"})
        ct = resp.headers.get("content-type", "")
        if resp.status_code == 200 and ct.startswith("image/"):
            return f"data:{ct.split(';')[0]};base64," + base64.b64encode(resp.content).decode()
    except Exception:  # noqa: BLE001 — best-effort; fall back to text-to-image
        return None
    return None


async def generate(prompt: str, reference: str | None = None) -> str | None:
    """Return an image URL or data URI, or None on any failure. When `reference`
    (a real photo URL) and a Pollinations token are both available, ground the
    image in the real location via image-to-image; otherwise text-to-image."""
    if PROVIDER == "pollinations":
        if reference and POLLINATIONS_TOKEN:
            data = await _pollinations_i2i(prompt, reference)
            if data:
                return data
        # keyless text-to-image — URL renders when the browser fetches it
        return _pollinations_url(prompt)
    if not API_KEY:
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
