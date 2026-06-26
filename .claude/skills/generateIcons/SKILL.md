---
name: generateIcons
description: Generate one ~400x400 Tolkien-style ICON per LOCATION that opts in via an `icon_description:` field in the game-data YAML `locations:` blocks, drawn from that text, using the Google Gemini image API (gemini-2.5-flash-image / "Nano Banana") and a Tolkien style reference image (maps/input/style/tolkiten_style.png). Locations without an icon_description are skipped. Each becomes a single hand-inked map emblem (aged sepia pen-and-ink) on a TRANSPARENT background (chroma keyed out). Output is one PNG per location id under maps/output/icons/ (or numbered variants with --n). Additive only — it renders ONLY missing icons and NEVER overwrites existing ones (delete a PNG to redo it). Use when asked to make/render/update location icons or "/generateIcons". Local to the OneLife repo.
---

# Generate location icons (generateIcons)

Renders **one Tolkien-style icon per location that opts in** — a location opts in by
giving its `locations:` entry an **`icon_description:`** field, and that text is the
icon's visual prompt (so the apothecary reads as a shuttered pharmacy, the crypt as a
Romanesque crypt, and so on). **Locations without an `icon_description` are skipped.**
Every icon is a single hand-inked emblem in the look of an old Tolkien fantasy map
(aged sepia pen-and-ink / engraving), matched to the style reference image, **with no
text**.

By default the emblem is drawn on a flat chroma-green background that is **keyed out
to a transparent alpha**, so each icon drops cleanly onto the world map or the UI.

**Each icon is generated one at a time, in its own fresh process** (a new SDK client
and a freshly loaded style image, nothing shared) so there is zero carry-over between
icons and each keeps its own character. (The Gemini image API is already stateless
per request; this makes the isolation absolute.) Pass `--no-isolate` to run them in a
single shared process instead — faster, but the icons share in-memory objects.

## Run it

From the repo root:

```bash
python .claude/skills/generateIcons/generate_icons.py            # fill in missing
python .claude/skills/generateIcons/generate_icons.py --n 5      # up to 5 variants each
python .claude/skills/generateIcons/generate_icons.py --only lund-crypt,lund-manor
python .claude/skills/generateIcons/generate_icons.py --dry-run  # list + sample prompt
```

- Output: **one PNG per location id** under `maps/output/icons/<location-id>.png`
  (with `--n>1`, numbered variants `<location-id>-v1.png` … `<location-id>-vN.png`).
- **Additive only — NEVER overwrites.** The skill renders *only icons that don't yet
  exist*; every existing PNG is left untouched. A re-run just fills any gaps. **To
  redo an icon, delete its PNG first**, then run again.
- `--dry-run` lists the locations and prints one sample prompt (no API key needed) —
  preview before spending calls.

### Options
- `--out DIR` — output directory (default `maps/output/icons`).
- `--size 400` — square icon size in px (default **400**).
- `--n N` — how many variants to make per location (default **1**). `--n 5` →
  five `<id>-v1..v5.png` per location; existing variants are kept, only missing
  ones are filled in.
- `--style PATH` — style reference (default `maps/input/style/tolkiten_style.png`).
- `--only IDS` — comma-separated location ids to consider (still skips existing ones).
- `--limit N` — process at most N missing variants (handy for a test batch).
- `--no-isolate` — render all icons in one shared process (faster) instead of the
  default fresh-process-per-icon isolation.
- `--keep-bg` — keep the flat green background instead of keying it transparent.
- `--bg-thresh N` — flood-fill colour tolerance for keying the background (default 60).
- `--extra "..."` — extra free-text instructions appended to every prompt.
- `--model NAME` — override the image model (default `gemini-2.5-flash-image`).
- `--content DIR` — game-data dir (default `$GAME_DATA_DIR` → `$CONTENT_DIR` →
  `../OneLife-KBK-mystery`).

## How it works
1. Merges every `*.yaml` in the data repo and collects each `locations:` entry that
   has an **`icon_description:`** (id + that text); locations without it are skipped.
2. For each location (and each requested variant), composes a prompt that renders the
   `icon_description` as ONE iconic map-emblem, forbids all text, and (unless
   `--keep-bg`) puts it on flat chroma green.
3. One Gemini call per icon with the style image as a reference, square (1:1) —
   each in its own fresh subprocess by default (no shared state), one at a time.
4. Centre-crops to square, resizes to `--size`, **keys the flat background to
   transparent** (border flood-fill, robust to the model's muted "green", plus
   green-spill suppression for clean edges), and saves a PNG.

## Prerequisites
- **Python deps** (host Python, not Docker): `pip install google-genai pillow pyyaml`.
- **API key**: set `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) in the environment, or add
  it to the repo `.env`. Get one at https://aistudio.google.com/apikey.
- **Style image** at `maps/input/style/tolkiten_style.png` (or pass `--style`).

## After generating
- Report how many icons were saved and where (`maps/output/icons/`).
- Image models are non-deterministic. If an icon shows stray text, a full scene
  instead of a single emblem, or a dark/green background that survived keying,
  **delete that PNG** and re-run (optionally with `--only <location-id>` and
  `--extra "..."`), or generate several `--n` variants and keep the best.
