---
name: mapsUpdate
description: Generate an old, worn, hand-drawn map of a given area/city using the Google Gemini image API (gemini-2.5-flash-image / "Nano Banana"), styled after a reference image (maps/input/style/style.png). Each requested highlight is drawn graphically prominent, clearly labelled in pen-and-ink, and appears exactly once. Output defaults to 4:3. Use when asked to make/update a map, a treasure-style map, or "/mapsUpdate". Local to the OneLife repo.
---

# Generate a hand-drawn map (mapsUpdate)

Turns an **area/city** + a **list of highlights** into a single illustrated map that
looks **old, weathered and hand-inked** (not printed). It calls the **Google Gemini
image API** (`gemini-2.5-flash-image`, aka "Nano Banana"), passing a **style
reference image** so the result matches a chosen look.

Each highlight is rendered to **pop out** of the map (bolder, larger, eye-catching),
gets a **handwritten pen-and-ink label**, and appears **exactly once**. **All text**
on the map is pen-and-ink lettering. Output is **4:3** unless another ratio is given.

A **world exit** is drawn at the edge by default: a signpost/gateway labelled "To the
Global Map" with a road leading off the edge, so it's clear the player can leave this
local map and travel on the wider global map. Customise with `--global-label`, or turn
it off with `--no-global-exit`.

## Inputs
- **Area / city** (required) — e.g. `"Lund"`, `"Malmö"`, `"the Cinque Terre coast"`.
- **Highlights** (optional, repeatable) — features to make prominent + label. **If
  omitted, they're auto-derived from the OneLife world graph** (see below).
- **Style image** — default `maps/input/style/style.png`; override with `--style`.
- **Aspect ratio** — default `4:3`; one of `1:1 2:3 3:2 3:4 4:3 4:5 5:4 9:16 16:9 21:9`.

## Auto-highlights from the world graph
When no highlights are passed, the script reads the game-data YAML and highlights
**every location that lives in the same world subgraph (cell) as the named area** —
i.e. all the places you can reach within that region. The area is resolved, in order,
by: cell **id/name** → cell **region** → a **location** id/name (uses that location's
cell) → fuzzy cell-name match.

Example: `--area "Lund"` auto-fills `The Cathedral Library, The Supper Club Cellar,
Holm's Lodgings, The Manor Outside Lund, The Botanical Garden, The Old Apothecary,
The Cathedral Crypt, Cathedral Square, Lund Station`. Naming any one of those places
(e.g. `--area "Lund Station"`) resolves to the same Lund cell.

- It finds the game-data automatically (`$GAME_DATA_DIR` → `$CONTENT_DIR` → sibling
  `../OneLife-KBK-mystery`); override with `--content DIR`.
- Passing any `--highlight`/`--highlights` **overrides** the auto-derivation.
- `--no-auto-highlights` disables it (general map, no marked places).
- If the area matches nothing (or PyYAML/data is unavailable), it warns and renders
  without highlights rather than failing. Needs `pyyaml`.

## Run it

From the repo root:

```bash
python .claude/skills/mapsUpdate/generate_map.py \
    --area "Lund, Sweden" \
    --highlight "Lund Cathedral" \
    --highlight "Lundagård park" \
    --highlight "The old water tower"
```

### Render every main location at once (`--all`)
`--all` renders **one local map per main location** — i.e. per world cell — each with
that cell's places auto-highlighted and an edge exit to the global map:

```bash
python .claude/skills/mapsUpdate/generate_map.py --all
```

- Output goes to **`<content>/images/maps/<cell-id>.png`** (e.g. the data repo's
  `images/maps/lund.png`, `malmo.png`, `sandby.png`, `skane-woods.png`). These are
  **stable filenames that overwrite** on re-run (it's an *update*). Override the
  destination with `--out DIR`.
- One map fails → the batch keeps going and reports a summary at the end.
- `--sleep N` waits N seconds between maps to ease API rate limits.
- `--dry-run` lists the planned maps (cell, filename, highlight count) without calling
  the API. Other flags (`--aspect`, `--style`, `--global-label`, `--no-global-exit`,
  `--model`, `--extra`) apply to every map in the batch.

Useful flags:
- `--highlights "a; b; c"` — shortcut for several highlights in one string.
- `--style PATH` — a different style reference (default `maps/input/style/style.png`).
- `--aspect 16:9` — change the output shape (default `4:3`).
- `--out PATH` — output file or directory (default `maps/output/`). A directory gets
  an auto-named file (`<area-slug>.png`); **existing files are never overwritten**
  (a `-2`, `-3`, … suffix is added).
- `--extra "..."` — append extra free-text instructions to the prompt.
- `--global-label "..."` — text for the edge world-exit (default `To the Global Map`).
- `--no-global-exit` — don't draw the edge exit + road to the global map.
- `--model NAME` — override the image model (default `gemini-2.5-flash-image`).
- `--dry-run` — print the composed prompt + config and exit (no API call, no key
  needed). Use this to preview/tune wording before spending a call.

## Prerequisites
- **Python deps** (host Python, not Docker): `pip install google-genai pillow`.
- **API key**: set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in the environment, or add
  it to the repo `.env`. Get one at https://aistudio.google.com/apikey.
- **Style image present** at `maps/input/style/style.png` (or pass `--style`).

The script fails fast with a clear message if the key, SDK, or style image is missing.

## How it works
1. Resolves the style image and reads the API key (env → repo `.env`).
2. Composes a prompt baking in every requirement: match the style reference; old/worn/
   weathered paper; hand-drawn & hand-inked (never printed); **all text** as pen-and-ink
   handwriting; each highlight prominent + labelled + **exactly once**; the chosen aspect.
3. Calls `client.models.generate_content(model=…, contents=[prompt, style_image], config=…)`
   with `response_modalities=["IMAGE"]` and `image_config.aspect_ratio` (the aspect is
   also stated in the prompt as a fallback for older SDK builds).
4. Saves the returned image to `maps/output/` and prints the path.

## After generating
- Report the saved path and tell the user to open the PNG in `maps/output/`.
- Image models are non-deterministic: if a highlight is missing/duplicated, the text
  isn't handwritten, or the look is too clean, re-run (each run is a fresh attempt) or
  tighten the wording via `--extra "..."`. Use `--dry-run` first to preview the prompt.
