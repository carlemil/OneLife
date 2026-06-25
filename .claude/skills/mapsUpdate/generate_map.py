#!/usr/bin/env python3
"""
mapsUpdate — generate an old, worn, hand-drawn map of an area/city with the
Google Gemini image API ("Nano Banana", model `gemini-2.5-flash-image`), using a
style reference image (default `maps/input/style/style.png`).

Every requested highlight is rendered graphically prominent, clearly visible,
labelled, and appears exactly once on the map. All written text is rendered as
pen-and-ink handwriting. Output defaults to 4:3.

Usage (from the repo root):

  python .claude/skills/mapsUpdate/generate_map.py \
      --area "Lund, Sweden" \
      --highlight "Lund Cathedral" \
      --highlight "Lundagård park" \
      --highlight "The old train station"

Options:
  --area TEXT          (required) the city/area/region to map.
  --highlight TEXT     a feature to make pop out + label; repeat for several.
                       (also accepts --highlights "a; b; c" as a shortcut)
  --style PATH         style reference image (default maps/input/style/style.png).
  --aspect RATIO       output aspect ratio (default 4:3). e.g. 16:9, 1:1, 3:2.
  --out PATH           output file or dir (default maps/output/). A directory
                       gets an auto-named file; existing files are never clobbered.
  --extra TEXT         extra free-text instructions appended to the prompt.
  --model NAME         override the image model (default gemini-2.5-flash-image).
  --dry-run            print the composed prompt + config and exit (no API call).

Requirements: pip install google-genai pillow
API key: set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment, or put it in
the repo .env. Get one at https://aistudio.google.com/apikey
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
from pathlib import Path

DEFAULT_MODEL = "gemini-2.5-flash-image"
DEFAULT_STYLE = "maps/input/style/style.png"
DEFAULT_OUT_DIR = "maps/output"
# Aspect ratios the model accepts (others are rejected by the API).
VALID_ASPECTS = {
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
}


def repo_root() -> Path:
    # .claude/skills/mapsUpdate/generate_map.py -> repo root is 3 parents up.
    return Path(__file__).resolve().parents[3]


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def find_content_dir(explicit: str | None) -> Path | None:
    """Locate the game-data repo: --content -> $GAME_DATA_DIR -> $CONTENT_DIR ->
    sibling ../OneLife-KBK-mystery. Returns None if none has *.yaml."""
    cands = [explicit, os.environ.get("GAME_DATA_DIR"), os.environ.get("CONTENT_DIR"),
             str(repo_root().parent / "OneLife-KBK-mystery")]
    for c in cands:
        if c and Path(c).is_dir() and list(Path(c).glob("*.yaml")):
            return Path(c)
    return None


def load_world(content_dir: Path) -> tuple[list, list]:
    """Merge every *.yaml in the data repo, return (cells, locations)."""
    import yaml  # optional dep; caller guards the ImportError
    cells: list = []
    locations: list = []
    for f in sorted(content_dir.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        cells += doc.get("cells") or []
        locations += doc.get("locations") or []
    return cells, locations


def derive_highlights(area: str, content_dir: Path) -> tuple[list[str], str | None]:
    """All location names that live in the same world subgraph (cell) as `area`.

    `area` is resolved to one or more cells: first by cell id/name, then by region,
    then by a location's id/name (-> that location's cell), then a fuzzy name
    contains. Returns (location_names_in_those_cells, human_label) or ([], None)."""
    cells, locations = load_world(content_dir)
    na = _norm(area)

    def cells_named(field_getters) -> set[str]:
        hits = set()
        for c in cells:
            if any(na == _norm(g(c)) for g in field_getters):
                hits.add(c.get("id"))
        return hits

    target = cells_named([lambda c: c.get("id"), lambda c: c.get("name")])
    if not target:
        target = cells_named([lambda c: c.get("region")])
    if not target:
        for l in locations:
            if na in (_norm(l.get("id")), _norm(l.get("name"))) and l.get("cell"):
                target.add(l.get("cell"))
    if not target:  # last resort: fuzzy contains on cell name/id
        for c in cells:
            cn, ci = _norm(c.get("name")), _norm(c.get("id"))
            if na and (na in cn or cn in na or na in ci):
                target.add(c.get("id"))
    if not target:
        return [], None

    names = [l.get("name") or l.get("id") for l in locations if l.get("cell") in target]
    label = ", ".join(sorted(_norm(next((c.get("name") or c.get("id")
                       for c in cells if c.get("id") == cid), cid)) for cid in target))
    return names, label


def load_api_key() -> str | None:
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        v = os.environ.get(var)
        if v:
            return v.strip()
    # Fall back to the repo .env (gitignored).
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


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "map"


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (repo_root() / path)


def pick_out_path(out_arg: str, area: str) -> Path:
    out = resolve_path(out_arg)
    if out.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        out.parent.mkdir(parents=True, exist_ok=True)
        target = out
    else:
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{slugify(area)}.png"
    # Never clobber: add -2, -3, ...
    if target.exists():
        stem, suf = target.stem, target.suffix
        n = 2
        while (target.parent / f"{stem}-{n}{suf}").exists():
            n += 1
        target = target.parent / f"{stem}-{n}{suf}"
    return target


def build_prompt(area: str, highlights: list[str], aspect: str, extra: str,
                 global_exit: str | None) -> str:
    if highlights:
        hl_lines = "\n".join(f"  {i}. {h}" for i, h in enumerate(highlights, 1))
        highlight_block = (
            f"\nMARK THESE {len(highlights)} HIGHLIGHTS — each one must be drawn so it "
            "POPS OUT of the map and is impossible to miss:\n"
            f"{hl_lines}\n"
            "For every highlight:\n"
            "  - Render it as a bold, eye-catching hand-drawn landmark/icon that is "
            "visually more prominent than the surrounding map detail (larger, darker "
            "ink, a little flourish or vignette) so it clearly stands out.\n"
            "  - Give it a clear hand-lettered label in pen-and-ink, legible and "
            "close to the landmark.\n"
            "  - Each highlight must appear EXACTLY ONCE on the map — never duplicated "
            "and never omitted.\n"
        )
    else:
        highlight_block = ""

    if global_exit:
        exit_block = (
            "\nWORLD EXIT — show that this local map is only one part of a larger "
            "world the player can travel to:\n"
            f"  - At the very EDGE of the map, draw a single signpost/gateway "
            f"landmark labelled \"{global_exit}\" in the same pen-and-ink hand.\n"
            "  - Draw a clear road/path that connects the local area's road network "
            "to this edge landmark and continues off the edge of the map, so it "
            "reads as the way OUT to the wider world map.\n"
            "  - Keep it distinct from the highlights above; it is a travel exit, "
            "not a place to visit. Show it exactly once.\n"
        )
    else:
        exit_block = ""

    extra_block = f"\nAdditional instructions: {extra}\n" if extra else ""

    return (
        f"Create a map of {area}.\n"
        "\n"
        "STYLE: Match the look and feel of the provided reference image — use it as "
        "the art-style guide for line work, palette, texture and overall mood. The "
        "map must look OLD, WEATHERED and WORN: aged/foxed paper or parchment, "
        "faded and stained, frayed or torn edges, creases — like a treasured old "
        "document, not a clean modern print.\n"
        "\n"
        "It must look HAND-DRAWN and HAND-INKED, never printed, vector or digital: "
        "visible, slightly irregular pen strokes, hand-shaded hills and water, "
        "organic wobble to coastlines, roads and borders.\n"
        "\n"
        "ALL written text on the map — every label, title, place name and note — "
        "must be HANDWRITTEN PEN-AND-INK lettering (as if dipped-pen calligraphy), "
        "never a typeset/computer font. Keep all text correctly spelled and legible.\n"
        f"{highlight_block}"
        f"{exit_block}"
        "\n"
        "Keep the geography of the area plausible and recognisable. Fill the whole "
        "frame; do not leave large empty margins.\n"
        f"Output the map in a {aspect} aspect ratio.\n"
        f"{extra_block}"
    )


def load_sdk():
    """Import the Gemini SDK + Pillow, or raise RuntimeError with a fix-it message."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError("the Gemini SDK is missing — install with: "
                           "pip install google-genai pillow")
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("Pillow is missing — install with: pip install pillow")
    return genai, types, Image


def make_config(types, aspect: str):
    """Request an image back in the given aspect ratio; degrade for older SDKs."""
    try:
        return types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect),
        )
    except (TypeError, AttributeError):
        try:
            return types.GenerateContentConfig(response_modalities=["IMAGE"])
        except (TypeError, AttributeError):
            return None


def generate_and_save(client, types, Image, model, prompt, style_img, aspect,
                      out_path: Path) -> tuple[bool, str | None]:
    """One Gemini call -> save image to out_path. Returns (ok, error_message)."""
    kwargs = {"model": model, "contents": [prompt, style_img]}
    config = make_config(types, aspect)
    if config is not None:
        kwargs["config"] = config
    try:
        response = client.models.generate_content(**kwargs)
    except Exception as e:
        return False, f"Gemini request failed: {e}"

    notes = []
    for cand in (response.candidates or []):
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                out_path.parent.mkdir(parents=True, exist_ok=True)
                Image.open(io.BytesIO(inline.data)).save(out_path)
                return True, None
            if getattr(part, "text", None):
                notes.append(part.text)
    msg = "the model returned no image"
    if notes:
        msg += " (model said: " + " ".join(notes).strip() + ")"
    return False, msg


def auto_highlights(area: str, explicit: list[str], content_dir: Path | None,
                    no_auto: bool) -> list[str]:
    """Resolve highlights for an area: explicit win; else derive from the world
    subgraph (the area's cell) unless disabled. Prints what it picked."""
    if explicit or no_auto:
        return list(explicit)
    if content_dir is None:
        print("NOTE: no highlights given and game-data dir not found "
              "(set GAME_DATA_DIR or pass --content) — no highlights.", file=sys.stderr)
        return []
    try:
        derived, label = derive_highlights(area, content_dir)
    except ImportError:
        print("NOTE: PyYAML missing — can't auto-derive highlights "
              "(pip install pyyaml).", file=sys.stderr)
        return []
    if derived:
        print(f"Auto-highlights for '{area}' (subgraph: {label}): "
              f"{len(derived)} place(s) -> {', '.join(derived)}")
        return derived
    if label is None:
        print(f"NOTE: '{area}' didn't match any cell/region/location in the world "
              f"graph — no highlights.", file=sys.stderr)
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a hand-drawn map via Gemini.")
    ap.add_argument("--area", help="city/area/region to map (required unless --all)")
    ap.add_argument("--all", action="store_true",
                    help="render one map per MAIN LOCATION (every world cell), into "
                         "<content>/images/maps (or --out DIR)")
    ap.add_argument("--highlight", action="append", default=[],
                    help="a feature to make pop out + label (repeatable)")
    ap.add_argument("--highlights", default="",
                    help="shortcut: highlights separated by ';'")
    ap.add_argument("--style", default=DEFAULT_STYLE, help="style reference image")
    ap.add_argument("--aspect", default="4:3", help="output aspect ratio")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR, help="output file or directory")
    ap.add_argument("--extra", default="", help="extra prompt instructions")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="image model id")
    ap.add_argument("--content", default=None,
                    help="game-data dir; default $GAME_DATA_DIR -> $CONTENT_DIR "
                         "-> ../OneLife-KBK-mystery")
    ap.add_argument("--no-auto-highlights", action="store_true",
                    help="don't auto-derive highlights from the world graph")
    ap.add_argument("--global-label", default="To the Global Map",
                    help="label for the edge world-exit (default 'To the Global Map')")
    ap.add_argument("--no-global-exit", action="store_true",
                    help="don't draw the edge exit + road to the global map")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="seconds to wait between maps in --all (ease rate limits)")
    ap.add_argument("--dry-run", action="store_true", help="print plan/prompt and exit")
    args = ap.parse_args()

    aspect = args.aspect.strip()
    if aspect not in VALID_ASPECTS:
        print(f"ERROR: aspect '{aspect}' not supported. Choose one of: "
              f"{', '.join(sorted(VALID_ASPECTS))}", file=sys.stderr)
        return 2
    global_exit = None if args.no_global_exit else args.global_label.strip()
    extra = args.extra.strip()

    extra_hl = list(args.highlight)
    if args.highlights:
        extra_hl += [h.strip() for h in args.highlights.split(";") if h.strip()]

    if args.all:
        return run_all(args, aspect, global_exit, extra)

    if not args.area:
        print("ERROR: --area is required (or use --all).", file=sys.stderr)
        return 2

    content_dir = find_content_dir(args.content)
    highlights = auto_highlights(args.area, extra_hl, content_dir, args.no_auto_highlights)
    prompt = build_prompt(args.area, highlights, aspect, extra, global_exit)

    if args.dry_run:
        print("=== MODEL ===\n" + args.model)
        print("=== ASPECT ===\n" + aspect)
        print("=== STYLE IMAGE ===\n" + str(resolve_path(args.style)))
        print("=== OUT ===\n" + str(pick_out_path(args.out, args.area)))
        print("=== PROMPT ===\n" + prompt)
        return 0

    style_path = resolve_path(args.style)
    if not style_path.exists():
        print(f"ERROR: style image not found: {style_path}\n"
              f"Put your style reference there, or pass --style PATH.", file=sys.stderr)
        return 2
    api_key = load_api_key()
    if not api_key:
        print("ERROR: no API key. Set GEMINI_API_KEY (or GOOGLE_API_KEY) in your "
              "environment or the repo .env.\nGet a key at "
              "https://aistudio.google.com/apikey", file=sys.stderr)
        return 2
    try:
        genai, types, Image = load_sdk()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    style_img = Image.open(style_path)
    client = genai.Client(api_key=api_key)
    out_path = pick_out_path(args.out, args.area)
    print(f"Generating map of '{args.area}' "
          f"({len(highlights)} highlight(s), {aspect}) with {args.model} ...")
    ok, err = generate_and_save(client, types, Image, args.model, prompt,
                                style_img, aspect, out_path)
    if not ok:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"\nSaved map -> {out_path}")
    return 0


def run_all(args, aspect: str, global_exit: str | None, extra: str) -> int:
    """Render one map per MAIN LOCATION (every world cell)."""
    import time
    content_dir = find_content_dir(args.content)
    if content_dir is None:
        print("ERROR: --all needs the game-data dir (set GAME_DATA_DIR or pass "
              "--content).", file=sys.stderr)
        return 2
    try:
        cells, _ = load_world(content_dir)
    except ImportError:
        print("ERROR: PyYAML missing — install with: pip install pyyaml",
              file=sys.stderr)
        return 2
    if not cells:
        print(f"ERROR: no cells (main locations) found in {content_dir}.",
              file=sys.stderr)
        return 2

    # Default destination: the data repo's images/maps (overridable with --out).
    out_dir = (resolve_path(args.out) if args.out != DEFAULT_OUT_DIR
               else content_dir / "images" / "maps")

    plan = []  # (cell_id, area_name, highlights, out_path)
    for c in cells:
        cid = c.get("id")
        area = c.get("name") or cid
        hl = auto_highlights(area, list(args.highlight), content_dir,
                             args.no_auto_highlights)
        plan.append((cid, area, hl, out_dir / f"{cid}.png"))

    print(f"\n{len(plan)} main location(s) -> {out_dir}")
    if args.dry_run:
        for cid, area, hl, out_path in plan:
            print(f"  - {area} [{cid}] -> {out_path.name}  ({len(hl)} highlight(s))")
        return 0

    style_path = resolve_path(args.style)
    if not style_path.exists():
        print(f"ERROR: style image not found: {style_path}", file=sys.stderr)
        return 2
    api_key = load_api_key()
    if not api_key:
        print("ERROR: no API key. Set GEMINI_API_KEY (or GOOGLE_API_KEY).",
              file=sys.stderr)
        return 2
    try:
        genai, types, Image = load_sdk()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    style_img = Image.open(style_path)
    client = genai.Client(api_key=api_key)
    out_dir.mkdir(parents=True, exist_ok=True)

    ok_n, fails = 0, []
    for i, (cid, area, hl, out_path) in enumerate(plan):
        print(f"\n[{i+1}/{len(plan)}] '{area}' "
              f"({len(hl)} highlight(s), {aspect}) -> {out_path.name}")
        prompt = build_prompt(area, hl, aspect, extra, global_exit)
        ok, err = generate_and_save(client, types, Image, args.model, prompt,
                                    style_img, aspect, out_path)
        if ok:
            ok_n += 1
            print(f"  saved -> {out_path}")
        else:
            fails.append((area, err))
            print(f"  FAILED: {err}", file=sys.stderr)
        if args.sleep and i < len(plan) - 1:
            time.sleep(args.sleep)

    print(f"\nDone: {ok_n}/{len(plan)} maps saved to {out_dir}")
    if fails:
        print("Failed:")
        for area, err in fails:
            print(f"  - {area}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
