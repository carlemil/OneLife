---
name: generateMap
description: Generate ONE big, old, hand-drawn WORLD map of the whole OneLife game from its content YAML, using the Google Gemini image API (gemini-2.5-flash-image / "Nano Banana") and a style reference image (maps/input/style/style.png). Every `type: location` node becomes one oversized iconic building/feature drawn from its description; nodes are clustered by cell and rendered as a city, a village (2–3 nodes), or wilderness, positioned by their saved map coordinates. NO text anywhere on the map; edges fade to transparent; ~2K resolution. Use when asked to make/update/render the world map or "/generateMap". Local to the OneLife repo.
---

# Generate the OneLife world map (generateMap)

Reads the game's content YAML and renders **one big illustrated world map** in a
single Gemini image call, styled after a reference image so it looks **old,
weathered and hand-inked**.

What it draws:

- **One iconic building or feature per `type: location` node.** Each is drawn from
  that location's **description** (so the apothecary looks like a shuttered pharmacy,
  the crypt like a Romanesque crypt, etc.) — as a **picture only, never labelled**.
- **Clusters by world cell**, positioned using each cell's and each location's
  **saved map coordinates** (falling back to the cell grid when a cell has no coords):
  - a **city** for a cell with more than 3 locations,
  - a **village** for a cell with only **2–3** locations,
  - **wilderness** (forest / wild scenery, *not* a town) for a cell that reads
    natural — `kind: wilderness` or a name/region like "Skåne Woods".
- The landmark buildings are deliberately **oversized** (an old pictorial map where
  the important places loom large); their scale is **not** consistent across the map.
- **No text at all** — no title, no labels, nothing written anywhere.
- **Transparent, feathered edges** (the PNG has an alpha channel that fades to 0 at
  the borders, so it composites cleanly) at **~2K** resolution.

## Run it

From the repo root:

```bash
python .claude/skills/generateMap/generate_map.py
```

- Output defaults to **`maps/output/world.png`** and **overwrites** on re-run (it's
  *the* world map). Override with `--out FILE|DIR`.
- Finds the game-data automatically (`$GAME_DATA_DIR` → `$CONTENT_DIR` → sibling
  `../OneLife-KBK-mystery`); override with `--content DIR`.
- `--dry-run` prints the cluster plan + full prompt (and needs no API key) — use it
  to preview before spending a call.

### Options
- `--out PATH` — output file or directory (default `maps/output/world.png`).
- `--size 2048` — long-edge resolution in px (default **2048**, ~2K).
- `--feather 0.06` — fraction of each side that fades to transparent (`0` disables).
- `--aspect 4:3` — output aspect ratio (default `4:3`; one of
  `1:1 2:3 3:2 3:4 4:3 4:5 5:4 9:16 16:9 21:9`).
- `--style PATH` — style reference image (default `maps/input/style/style.png`).
- `--extra "..."` — extra free-text instructions appended to the prompt.
- `--model NAME` — override the image model (default `gemini-2.5-flash-image`).
- `--content DIR` — game-data dir.

## Prerequisites
- **Python deps** (host Python, not Docker): `pip install google-genai pillow pyyaml`.
- **API key**: set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in the environment, or add
  it to the repo `.env`. Get one at https://aistudio.google.com/apikey.
- **Style image present** at `maps/input/style/style.png` (or pass `--style`).

The script fails fast with a clear message if the key, SDK, or style image is missing.

## How it works
1. Merges every `*.yaml` in the data repo and collects all `type: location` nodes,
   grouping them by cell with each cell's/location's saved coordinates and the
   location descriptions.
2. Classifies each cluster (city / village / wilderness) and composes ONE prompt
   that lays the clusters out by position, draws each location's oversized iconic
   building from its description, forbids all text, and asks the art to fade at the
   edges.
3. Calls `client.models.generate_content(model=…, contents=[prompt, style_image], …)`
   with `response_modalities=["IMAGE"]` and the aspect ratio.
4. Resizes to ~2K, **feathers the edges to transparent** (Pillow alpha mask), and
   saves a PNG.

## After generating
- Report the saved path and tell the user to open `maps/output/world.png`.
- Image models are non-deterministic and won't honour positions/clusters *exactly*.
  If clusters merge together, a place is missing/duplicated, or any stray text
  appears, **re-run** (each run is a fresh attempt) or tighten the wording via
  `--extra "..."`. Use `--dry-run` first to preview the plan.
