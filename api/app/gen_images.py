"""Pre-generate (warm) the location image cache.

  python -m app.gen_images           cache a provider image URL for every distinct
                                      media image_theme (skips already-cached themes)
  python -m app.gen_images --force    regenerate even if already cached

Uses the configured image provider (set IMAGE_PROVIDER=pollinations for the keyless
Pollinations backend). No-op if no provider is configured. Reuses
atmosphere.image_for, so the prompt and `generated_images` cache match what the
live /api/atmosphere endpoint serves.
"""
import asyncio
import sys

from . import content, db, atmosphere, imagegen, gamestate


def _theme_to_scene(data: dict) -> dict:
    """Map each image_theme to a representative scene (the first node that uses it):
    its location plus the node's media (real_place, reference photo) and body text.
    Themes live on nodes' `media`; their `location` gives the place to describe."""
    locs = {l["id"]: l for l in data["locations"]}
    scenes: dict[str, dict] = {}
    for n in data["nodes"]:
        media = n.get("media") or {}
        theme = media.get("image_theme")
        loc = locs.get(n.get("location"))
        if theme and loc and theme not in scenes:
            scenes[theme] = {
                "loc": loc,
                "real_place": media.get("real_place"),
                "reference": media.get("reference_image"),
                "scene_text": n.get("body"),
            }
    return scenes


async def run(force: bool) -> int:
    if not imagegen.CONFIGURED:
        print("No image provider configured "
              "(set IMAGE_PROVIDER=pollinations or IMAGE_API_KEY). Nothing to do.")
        return 0

    game_id = gamestate.active_game()
    data, _ = content.load_dir()
    cells = {c["id"]: c for c in data["cells"]}
    scenes = _theme_to_scene(data)
    if not scenes:
        print("No image themes found in content.")
        return 0

    mode = "image-to-image (real photos)" if imagegen.I2I_ENABLED else "text-to-image"
    print(f"Provider: {imagegen.PROVIDER} [{mode}]. {len(scenes)} distinct image theme(s).")
    pool = await db.get_pool()
    made = skipped = 0
    async with pool.acquire() as conn:
        if force:
            await conn.execute(
                "DELETE FROM generated_images WHERE game_id=$1 AND theme = ANY($2::text[])",
                game_id, list(scenes))
        for theme, s in sorted(scenes.items()):
            loc = s["loc"]
            setting = atmosphere.setting_for(loc, cells.get(loc.get("cell")))
            ref = s["reference"] if imagegen.I2I_ENABLED else None
            url = await atmosphere.image_for(
                conn, game_id, theme, loc, setting,
                real_place=s["real_place"], reference=ref, scene_text=s["scene_text"])
            if url:
                made += 1
                tag = "i2i " if url.startswith("data:") else "t2i "
                print(f"  OK {tag}{theme:<24} {('ref:' + s['reference'][:60]) if ref else url[:70]}")
            else:
                skipped += 1
                print(f"  SKIP {theme:<24} (no url returned)")
    await db.close_pool()
    print(f"Done. {made} cached, {skipped} skipped, {len(scenes)} themes total.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(force="--force" in sys.argv)))
