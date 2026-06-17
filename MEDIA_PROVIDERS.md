# OneLife — Real Media Providers

Upgrades the placeholders in [ATMOSPHERE.md](ATMOSPHERE.md) to real providers:
generated **location images** and full-track **Spotify** playback. Both are
optional and degrade cleanly to the built-in fallbacks.

---

## Image generation

`imagegen.py` calls a configurable **OpenAI-compatible images API** (`POST
/images/generations`). Anthropic has no image API, so this is a separate
provider — only images go through it; all text stays on Claude.

```
IMAGE_API_KEY=...                         # enables real generation
IMAGE_API_BASE=https://api.openai.com/v1  # any OpenAI-compatible endpoint
IMAGE_MODEL=gpt-image-1
```

- The atmosphere endpoint returns `image_url` (a remote URL or a `data:` URI)
  when configured, else `image_svg` (the deterministic procedural banner). The
  frontend prefers `image_url`.
- Generated images are **cached per theme** in `generated_images`, so each theme
  is generated once and reused across similar locations (and across restarts).
- The prompt is built from the location name/description, the cell setting, and
  the mood theme: *"Atmospheric establishing shot … Setting: <place> · <region> ·
  1992 … cinematic, desaturated, film grain, no text, no people."*
- Best-effort: any provider error falls back to the SVG.

> Not verified headlessly (needs a provider key). The **fallback path is tested**;
> the real path is wired to the standard images API shape.

## Spotify Web Playback SDK (full tracks)

`web/src/lib/spotify.js` adds **full-track playback** for players who connect a
Spotify **Premium** account, on top of the existing 30s-preview crossfade.

Flow:
1. `GET /api/spotify/config` exposes the public `client_id`.
2. **Connect Spotify** runs an **Authorization Code + PKCE** flow entirely in the
   browser (no client secret) — scopes `streaming`,
   `user-modify-playback-state`, `user-read-email`, `user-read-private`.
3. The **Web Playback SDK** registers a browser device; on each location change
   the game fades the player down, starts the cell's track
   (`PUT /me/player/play`), and fades back up — a crossfade-style transition.
4. Tokens are refreshed via the PKCE refresh token.

Setup: set `SPOTIFY_CLIENT_ID` (and `SPOTIFY_CLIENT_SECRET` for server-side
search), and **register a redirect URI** in the Spotify dashboard. Set
`SPOTIFY_REDIRECT_URI` in `.env` to that **exact** value so the browser flow
matches it — `/api/spotify/config` hands it to the client, which falls back to
its own origin + path when the var is empty. Spotify may require a loopback IP
(`http://127.0.0.1:5173/`) rather than `http://localhost:5173/`; whichever you
register, set the same value here and open the game at that origin.

Degradation:
- Not connected / no Premium → **30s preview crossfade** (if previews exist).
- No Spotify keys → text-only track suggestions.

> Not verified headlessly (needs a Premium login + registered redirect URI in a
> real browser). Implemented to Spotify's documented PKCE + Web Playback SDK API.

---

## Why these stay optional

Each real provider needs an account/key/Premium the deployment may not have, and
both have zero-config fallbacks that keep the game fully playable. Enable them per
environment via `.env`.

## Deferred

- Persisting generated images to object storage (today: cached as URL/data-URI in
  Postgres; large `data:` URIs are heavy).
- True overlapping crossfade (the SDK is a single player → volume-dip transition).
- Image moderation / regeneration controls; per-time-of-day image variants.
