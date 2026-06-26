---
name: generateParchment
description: Generate ONE empty, aged, torn parchment sheet (NO content at all — wear and tear only) using the Google Gemini image API (gemini-2.5-flash-image / "Nano Banana"). Blank weathered/foxed parchment with stains, creases and ragged torn edges, ~2K resolution, 4:3 by default. Useful as a backdrop/texture to composite a map, document or UI on top of. Use when asked to make/render an empty parchment, aged paper, or "/generateParchment". Local to the OneLife repo.
---

# Generate an empty parchment (generateParchment)

Renders **one blank, ancient-looking parchment sheet** in a single Gemini image
call — **no content whatsoever**, just wear and tear: foxing, water stains, age
spots, fold creases, surface grain, and **rough torn/ragged edges** with a few
nibbled corners, against a near-black background showing through the torn gaps.

It's the sibling of the `generateMap` skill (same Gemini infra) but draws *nothing*
on the page — handy as a backdrop/texture to composite a map, letter or UI over.

## Run it

From the repo root:

```bash
python .claude/skills/generateParchment/generate_parchment.py
```

- Output defaults to **`maps/output/parchment.png`** and **overwrites** on re-run.
  Override with `--out FILE|DIR`.
- `--dry-run` prints the full prompt (and needs no API key) — preview before
  spending a call.

### Options
- `--out PATH` — output file or directory (default `maps/output/parchment.png`).
- `--size 2048` — long-edge resolution in px (default **2048**, ~2K).
- `--aspect 4:3` — output aspect ratio (default `4:3`; one of
  `1:1 2:3 3:2 3:4 4:3 4:5 5:4 9:16 16:9 21:9`).
- `--extra "..."` — extra free-text instructions appended to the prompt.
- `--model NAME` — override the image model (default `gemini-2.5-flash-image`).

## Prerequisites
- **Python deps** (host Python, not Docker): `pip install google-genai pillow`.
- **API key**: set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in the environment, or add
  it to the repo `.env`. Get one at https://aistudio.google.com/apikey.

The script fails fast with a clear message if the key or SDK is missing.

## After generating
- Report the saved path and tell the user to open `maps/output/parchment.png`.
- Image models are non-deterministic. If any stray text, deliberate marks or corner
  decoration appears (the model sometimes adds "old document" flourishes), **re-run**
  (each run is a fresh attempt) or tighten the wording via `--extra "..."`.
