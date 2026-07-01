"""Per-location atmosphere: a generated image + an LLM-picked Spotify soundtrack.

Image: a deterministic procedural SVG keyed by the location's media theme (a seam
for a real image-gen provider — Anthropic has no image API). Cached per theme.

Audio: an LLM "music director" (see llm.suggest_tracks) picks real songs fitting
the location and the fixed setting (southern Sweden, 1992). If Spotify app
credentials are configured, each pick is resolved to a real track (preview URL,
album art, link) via the Spotify Web API client-credentials flow — no user login
needed. The frontend crossfades the 30s previews as the player moves.
"""
import os
import time
import base64
import hashlib

import httpx

from . import llm, imagegen, gameconfig

_SP_ID = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
_SP_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
SPOTIFY_CONFIGURED = bool(_SP_ID and _SP_SECRET)

_img_cache: dict[str, str] = {}
_track_cache: dict[str, list] = {}
_token = {"value": None, "exp": 0.0}


# --------------------------------------------------------------------------- #
#  Image — deterministic procedural banner per theme (placeholder for real gen)
# --------------------------------------------------------------------------- #
def image_svg(theme: str) -> str:
    theme = theme or "default"
    if theme in _img_cache:
        return _img_cache[theme]
    h = int(hashlib.md5(theme.encode()).hexdigest(), 16)
    base = h % 360
    c1 = f"hsl({base},25%,8%)"
    c2 = f"hsl({(base + 30) % 360},30%,18%)"
    glow = f"hsl({(base + 60) % 360},40%,32%)"
    shapes = ""
    for i in range(3):
        cx = (h >> (i * 5)) % 800
        cy = 50 + ((h >> (i * 7)) % 120)
        r = 70 + ((h >> (i * 3)) % 130)
        shapes += f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r // 2}" fill="{glow}" opacity="0.12"/>'
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 260" '
        'preserveAspectRatio="xMidYMid slice">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{c2}"/><stop offset="1" stop-color="{c1}"/></linearGradient>'
        '<radialGradient id="v" cx="50%" cy="40%" r="75%">'
        '<stop offset="60%" stop-color="#000" stop-opacity="0"/>'
        '<stop offset="100%" stop-color="#000" stop-opacity="0.55"/></radialGradient></defs>'
        f'<rect width="800" height="260" fill="url(#g)"/>{shapes}'
        '<rect width="800" height="260" fill="url(#v)"/></svg>'
    )
    _img_cache[theme] = svg
    return svg


def setting_for(loc, cell=None, game_id=None) -> str:
    """"<place> · <region> · <period>" for the music director / image prompts. Region
    falls back from the cell to the game's configured setting.region; the game's
    setting.period (e.g. a year) is appended when present. All of it is dataset config
    (the engine hardcodes no place or year)."""
    s = (gameconfig.for_game(game_id) if game_id else gameconfig.active()).get("setting", {})
    place = loc["name"] if loc else "Unknown"
    region = (cell["region"] if cell and cell["region"] else s.get("region")) or ""
    period = (s.get("period") or "").strip()
    parts = [place] + [p for p in (region, period) if p]
    return " · ".join(parts)


# House look for every location image: retro, monochrome sepia (dark brown ->
# pale yellow/amber), old-photograph feel. Applied to text-to-image and, when a
# real reference photo + token are configured, to the restyled real photo too.
IMAGE_STYLE = (
    "Retro vintage monochrome duotone in a dark-brown to pale yellow/amber sepia "
    "palette only, no other colours. Aged faded old-photograph look, soft film "
    "grain, high contrast, weathered, moody, no text, no people."
)


async def image_for(conn, game_id, theme, loc, setting,
                    real_place=None, reference=None, scene_text=None) -> str | None:
    """A real generated image for this theme (cached), or None to use the SVG.

    The prompt blends the real place (`real_place`, falling back to the location
    name) with the node's in-game text (`scene_text`, falling back to the location
    description) under the house IMAGE_STYLE. If `reference` (a real photo URL) and
    a Pollinations token are configured, the image is grounded in that real photo
    via image-to-image; otherwise it is text-to-image of the named place."""
    if not imagegen.CONFIGURED or loc is None:
        return None
    row = await conn.fetchrow(
        "SELECT image_url FROM generated_images WHERE game_id=$1 AND theme=$2", game_id, theme)
    if row:
        return row["image_url"]
    place = real_place or loc["name"]
    scene = (scene_text or loc["description"] or "").strip()[:400]
    prompt = (
        f"Establishing shot of {place}. {scene} "
        f"Setting: {setting}. Mood: {theme}. {IMAGE_STYLE}"
    )
    url = await imagegen.generate(prompt, reference=reference)
    if url:
        await conn.execute(
            """INSERT INTO generated_images (game_id, theme, image_url) VALUES ($1,$2,$3)
               ON CONFLICT (game_id, theme) DO UPDATE SET image_url=EXCLUDED.image_url""",
            game_id, theme, url)
    return url


# --------------------------------------------------------------------------- #
#  Audio — music director + Spotify resolution
# --------------------------------------------------------------------------- #
async def tracks_for(location_id, name, description, theme, setting) -> list[dict]:
    if location_id in _track_cache:
        return _track_cache[location_id]
    picks = await llm.suggest_tracks(name, description, theme, setting)
    resolved = await _spotify_resolve(picks)
    _track_cache[location_id] = resolved
    return resolved


async def _spotify_token() -> str | None:
    if not SPOTIFY_CONFIGURED:
        return None
    now = time.time()
    if _token["value"] and _token["exp"] > now + 10:
        return _token["value"]
    creds = base64.b64encode(f"{_SP_ID}:{_SP_SECRET}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {creds}"},
                data={"grant_type": "client_credentials"})
        r.raise_for_status()
        j = r.json()
    except Exception:  # noqa: BLE001
        return None
    _token["value"] = j["access_token"]
    _token["exp"] = now + j.get("expires_in", 3600)
    return _token["value"]


async def _spotify_resolve(tracks: list[dict]) -> list[dict]:
    tok = await _spotify_token()
    if not tok:
        return tracks
    out = []
    async with httpx.AsyncClient(timeout=10) as c:
        for t in tracks:
            q = f"{t.get('artist', '')} {t.get('title', '')}".strip()
            try:
                r = await c.get(
                    "https://api.spotify.com/v1/search",
                    headers={"Authorization": f"Bearer {tok}"},
                    params={"q": q, "type": "track", "limit": 1})
                items = r.json().get("tracks", {}).get("items", [])
            except Exception:  # noqa: BLE001
                items = []
            if items:
                it = items[0]
                imgs = it.get("album", {}).get("images", [])
                out.append({
                    **t,
                    "matched": f'{it["artists"][0]["name"]} — {it["name"]}',
                    "spotify_id": it["id"],
                    "spotify_url": it["external_urls"]["spotify"],
                    "uri": it["uri"],
                    "preview_url": it.get("preview_url"),
                    "album_art": imgs[-1]["url"] if imgs else None,
                })
            else:
                out.append(t)
    return out
