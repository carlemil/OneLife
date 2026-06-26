#!/usr/bin/env python3
"""generateParchment — generate ONE empty, aged, torn parchment sheet (NO content
at all) via the Google Gemini image API ("Nano Banana", gemini-2.5-flash-image).

Reuses the same Gemini image infra as the generateMap skill, but the prompt asks
for a blank weathered/torn parchment — wear and tear ONLY, nothing drawn on it.
Useful as a backdrop/texture (e.g. to composite a map or UI on top of).

Usage (from the repo root):

  python .claude/skills/generateParchment/generate_parchment.py
  python .claude/skills/generateParchment/generate_parchment.py --aspect 16:9 --size 2560

Options:
  --out PATH       output file or directory (default maps/output/parchment.png;
                   a directory gets parchment.png). OVERWRITES on re-run.
  --size N         long-edge resolution in px (default 2048, ~2K).
  --aspect RATIO   output aspect ratio (default 4:3).
  --extra TEXT     extra free-text instructions appended to the prompt.
  --model NAME     override the image model (default gemini-2.5-flash-image).
  --keep-bg        keep the flat dark background opaque (default: key it out to a
                   TRANSPARENT alpha, so only the torn parchment sheet remains).
  --bg-thresh N    luminance (0-255) below which a border-connected pixel counts as
                   background to make transparent (default 70).
  --dry-run        print the composed prompt and exit (no API call, no key needed).

Requirements: pip install google-genai pillow
API key: set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment or the repo .env.
Get one at https://aistudio.google.com/apikey
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

DEFAULT_MODEL = "gemini-2.5-flash-image"
DEFAULT_OUT = "maps/output/parchment.png"
DEFAULT_SIZE = 2048          # ~2K on the long edge
# Aspect ratios the model accepts (others are rejected by the API).
VALID_ASPECTS = {
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
}


def repo_root() -> Path:
    # .claude/skills/generateParchment/generate_parchment.py -> repo root is 3 up.
    return Path(__file__).resolve().parents[3]


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (repo_root() / path)


def pick_out_path(out_arg: str) -> Path:
    out = resolve_path(out_arg)
    if out.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        out.parent.mkdir(parents=True, exist_ok=True)
        return out
    out.mkdir(parents=True, exist_ok=True)
    return out / "parchment.png"


def load_api_key() -> str | None:
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        v = os.environ.get(var)
        if v:
            return v.strip()
    env = repo_root() / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, val = line.partition("=")
            if k.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                return val.strip().strip('"').strip("'")
    return None


def key_background_to_alpha(Image, im, bg_thresh: int = 70, feather: float = 1.2):
    """Make the flat dark background TRANSPARENT, keeping the parchment opaque.

    Flood-fills the dark region inward from the image borders, so only background
    that actually touches an edge is removed — interior dark stains/foxing on the
    parchment stay opaque. Returns an RGBA image. Uses Pillow's C flood fill (fast).
    """
    from PIL import ImageDraw, ImageChops, ImageFilter

    work = im.convert("RGB")
    w, h = work.size
    gray = work.convert("L")
    sentinel = (255, 0, 255)  # pure magenta never occurs in sepia parchment

    # Seed from the border (corners + edge midpoints); only where it's truly dark,
    # then let the fill spread across the connected dark background.
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
             (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for sx, sy in seeds:
        if gray.getpixel((sx, sy)) < bg_thresh:
            ImageDraw.floodfill(work, (sx, sy), sentinel, thresh=bg_thresh)

    # Filled pixels are now exactly the sentinel colour -> alpha 0; everything else
    # opaque. A light blur on the alpha anti-aliases the torn edge for clean compositing.
    diff = ImageChops.difference(work, Image.new("RGB", work.size, sentinel)).convert("L")
    alpha = diff.point(lambda p: 0 if p == 0 else 255)
    if feather and feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))

    out = im.convert("RGBA")
    out.putalpha(alpha)
    return out


def build_prompt(aspect: str, extra: str) -> str:
    extra_block = f"\nAdditional instructions: {extra}\n" if extra else ""
    return (
        "Create a single image of an OLD, EMPTY sheet of parchment / aged paper — "
        "and absolutely NOTHING else.\n"
        "\n"
        "CRITICAL — COMPLETELY BLANK: there must be NO content of any kind on the "
        "parchment: no text, no letters, no numbers, no map, no drawing, no lines, no "
        "border, no frame, no corner flourishes or decorative scrollwork, no symbols, "
        "no illustration, no stains shaped like anything. Just bare parchment. Any "
        "deliberate-looking mark is a mistake — do NOT add one.\n"
        "\n"
        "The parchment must look ANCIENT, WEATHERED and WORN — wear and tear only:\n"
        "  - aged, foxed, uneven cream-to-tan parchment colour with soft mottling;\n"
        "  - water stains, age spots, faint discoloration and patchy darkening;\n"
        "  - fine creases, fold lines and surface crinkle/grain;\n"
        "  - ROUGH, TORN, RAGGED, frayed and irregular edges (some small rips and a "
        "few nibbled/missing corners), as if the sheet is brittle with age;\n"
        "  - subtle scuffs, scratches and a little grunge across the surface.\n"
        "\n"
        "Fill most of the frame with the parchment sheet itself (the torn edges of "
        "the parchment reach close to the image borders); the area around and through "
        "the torn gaps is a FLAT, UNIFORM, solid PURE BLACK background (no texture, no "
        "vignette, no gradient there) so it can be cleanly keyed out. Top-down flat "
        "view, even soft lighting, no cast shadows of other objects, no hands, no desk "
        "props.\n"
        f"Output in a {aspect} aspect ratio.\n"
        f"{extra_block}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate one empty, aged, torn parchment sheet via Gemini.")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output file or directory")
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE,
                    help="long-edge resolution in px (default 2048, ~2K)")
    ap.add_argument("--aspect", default="4:3", help="output aspect ratio")
    ap.add_argument("--extra", default="", help="extra prompt instructions")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="image model id")
    ap.add_argument("--keep-bg", action="store_true",
                    help="keep the dark background opaque (default: key it to transparent)")
    ap.add_argument("--bg-thresh", type=int, default=70,
                    help="luminance below which border-connected pixels become transparent")
    ap.add_argument("--dry-run", action="store_true", help="print prompt and exit")
    args = ap.parse_args()

    aspect = args.aspect.strip()
    if aspect not in VALID_ASPECTS:
        print(f"ERROR: aspect '{aspect}' not supported. Choose one of: "
              f"{', '.join(sorted(VALID_ASPECTS))}", file=sys.stderr)
        return 2

    prompt = build_prompt(aspect, args.extra.strip())
    out_path = pick_out_path(args.out)

    if args.dry_run:
        print("=== MODEL ===\n" + args.model)
        print(f"=== SIZE/ASPECT ===\n{args.size}px long edge · {aspect}")
        print("=== OUT ===\n" + str(out_path))
        print("=== PROMPT ===\n" + prompt)
        return 0

    api_key = load_api_key()
    if not api_key:
        print("ERROR: no API key. Set GEMINI_API_KEY (or GOOGLE_API_KEY) in your "
              "environment or the repo .env.\nGet a key at "
              "https://aistudio.google.com/apikey", file=sys.stderr)
        return 2
    try:
        from google import genai
        from google.genai import types
        from PIL import Image
    except ImportError as e:
        print(f"ERROR: missing deps ({e}). Install with: pip install google-genai pillow",
              file=sys.stderr)
        return 2

    client = genai.Client(api_key=api_key)
    try:
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect),
        )
    except (TypeError, AttributeError):
        try:
            config = types.GenerateContentConfig(response_modalities=["IMAGE"])
        except (TypeError, AttributeError):
            config = None

    print(f"Generating empty parchment ({aspect}, ~{args.size}px) with {args.model} ...")
    kwargs = {"model": args.model, "contents": [prompt]}
    if config is not None:
        kwargs["config"] = config
    try:
        resp = client.models.generate_content(**kwargs)
    except Exception as e:
        print(f"ERROR: Gemini request failed: {e}", file=sys.stderr)
        return 1

    notes = []
    for cand in (resp.candidates or []):
        for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                im = Image.open(io.BytesIO(inline.data)).convert("RGB")
                w, h = im.size
                if args.size and max(w, h) != args.size:
                    s = args.size / float(max(w, h))
                    im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
                if not args.keep_bg:
                    im = key_background_to_alpha(Image, im, args.bg_thresh)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                im.save(out_path)   # .png keeps the alpha (transparent background)
                bg = "opaque bg" if args.keep_bg else "transparent bg"
                print(f"\nSaved parchment -> {out_path}  ({im.size[0]}x{im.size[1]}, {bg})")
                return 0
            if getattr(part, "text", None):
                notes.append(part.text)
    msg = "the model returned no image"
    if notes:
        msg += " (model said: " + " ".join(notes).strip() + ")"
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
