# OneLife — Atmosphere (image + Spotify soundtrack)

Realizes the mood pillar from [GAME_DESIGN.md](GAME_DESIGN.md) §8: each location
gets a generated **image** and a fitting **soundtrack**. Audio is delivered as
**Spotify suggestions** chosen by the LLM, not generated audio — toggle Spotify
on and the game crossfades between tracks as you move.

---

## Setting

The world is set in **southern Sweden**. **Location** is what varies (today:
Killebäckskolan; traveling the world map changes the place). The music director is
given `"<location> · <region>"` so its picks fit the place. (Time/era is no longer
pinned — it was forced to 1992 previously.)

---

## Image (per location)

A deterministic **procedural SVG** keyed by the location's `media.image_theme`
(`atmosphere.image_svg`): a themed gradient + soft glows + vignette, cached per
theme and **reused across similar locations** (design §8). It renders as a banner
above the text stream.

This is a **placeholder with a clean seam** — Anthropic has no image API, so a
real image-gen provider would replace just `image_svg()`. The deterministic
version keeps the game zero-config and gives every theme a stable look.

## Audio (Spotify, opt-in)

When the player toggles **Spotify on**:

1. **Music director** (`llm.suggest_tracks`, `claude-haiku-4-5`) picks ~4 real
   songs that fit the location and the 1992-southern-Sweden setting, each with a
   one-line reason. (Offline stub picks a few moody tracks if no Anthropic key.)
2. **Resolution** — if `SPOTIFY_CLIENT_ID`/`SPOTIFY_CLIENT_SECRET` are set, each
   pick is resolved via the Spotify Web API **client-credentials** flow (no user
   login) to a real track: 30s `preview_url`, album art, and a Spotify link.
3. **Crossfade** — the frontend plays the first track with a preview and
   **crossfades** (1.5s volume ramp between two `<audio>` elements) to the new
   location's track whenever the location changes. Picks are cached per location.

Graceful degradation:
- **No Spotify keys:** the LLM's picks still show as text (artist/title + reason);
  no playback.
- **No `preview_url`** (Spotify omits it for some tracks/apps): the UI falls back
  to a Spotify **embed iframe** for the top track — it plays (full track for
  logged-in users, else a preview) but can't be programmatically crossfaded.

Endpoint: `GET /api/atmosphere?spotify=<0|1>` (auth + onboarded) →
`{image_svg, setting, theme, tracks, spotify_configured}`.

---

## Enable real Spotify playback

Create an app at developer.spotify.com, then in `.env`:

```
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
```

`docker compose up -d` and toggle Spotify in-game.

---

## Deferred

- **Full-track gapless playback** needs the Spotify **Web Playback SDK** + user
  OAuth + Premium; the slice uses 30s previews (or embeds) instead.
- Real image generation provider + persistent (DB/object-store) image cache.
- Caching tracks/images in the DB rather than in-process (resets on restart).
