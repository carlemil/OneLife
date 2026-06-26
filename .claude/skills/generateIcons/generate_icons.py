#!/usr/bin/env python3
"""generateIcons — generate one ~400x400 Tolkien-style ICON per LOCATION that opts in
via an `icon_description:` in the game-data YAML, via the Google Gemini image API
("Nano Banana", gemini-2.5-flash-image) and a style reference image.

A location opts in to an icon by giving its `locations:` entry an `icon_description:`
field; that text is the icon's visual prompt. Each becomes a single hand-inked emblem
in the look of an old Tolkien fantasy map (aged ink linework / engraving) — so the
apothecary reads as a shuttered pharmacy, the crypt as a Romanesque crypt, etc.
Locations WITHOUT an icon_description are skipped. The subject is drawn on a flat
chroma-green background that is keyed out to a TRANSPARENT alpha, so each icon
composites cleanly onto a map or UI.

Usage (from the repo root):

  python .claude/skills/generateIcons/generate_icons.py            # fill in missing
  python .claude/skills/generateIcons/generate_icons.py --n 5      # up to 5 variants each
  python .claude/skills/generateIcons/generate_icons.py --only lund-crypt,lund-manor
  python .claude/skills/generateIcons/generate_icons.py --dry-run  # list + sample prompt

This skill NEVER overwrites an existing icon — it only renders icons that are
missing. To redo one, delete its PNG first, then run again.

Options:
  --out DIR        output directory (default maps/output/icons). One PNG per location.
  --size N         square icon size in px (default 400).
  --n N            how many variants to make per location (default 1). With --n>1 the
                   files are <id>-v1.png .. <id>-vN.png; with --n 1 just <id>.png.
                   Existing variant files are kept; only missing ones are filled in.
  --style PATH     style reference image (default maps/input/style/tolkiten_style.png).
  --only IDS       comma-separated location ids to consider (still skips existing ones).
  --limit N        only process the first N missing variants (handy for a test batch).
  --keep-bg        keep the flat green background (default: key it to transparent).
  --extra TEXT     extra free-text instructions appended to every prompt.
  --model NAME     override the image model (default gemini-2.5-flash-image).
  --content DIR    game-data dir (default $GAME_DATA_DIR -> $CONTENT_DIR ->
                   ../OneLife-KBK-mystery).
  --dry-run        list the locations + print one sample prompt and exit (no API call).

Requirements: pip install google-genai pillow pyyaml
API key: set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment or the repo .env.
Get one at https://aistudio.google.com/apikey
"""
from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MODEL = "gemini-2.5-flash-image"
DEFAULT_STYLE = "maps/input/style/tolkiten_style.png"
DEFAULT_OUT_DIR = "maps/output/icons"
DEFAULT_SIZE = 400


def repo_root() -> Path:
    # .claude/skills/generateIcons/generate_icons.py -> repo root is 3 parents up.
    return Path(__file__).resolve().parents[3]


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (repo_root() / path)


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


def load_locations(content_dir: Path) -> list[dict]:
    """Merge every *.yaml and return one entry per LOCATION that OPTS IN to an icon.

    A location opts in by declaring an `icon_description:` in its `locations:` entry;
    that text is the icon's visual prompt. Locations without it are skipped (no icon).
    Returns {id, name, desc} where desc = icon_description. De-dupes by location id."""
    import yaml  # optional dep; caller guards the ImportError
    out: list[dict] = []
    seen: set = set()
    for f in sorted(content_dir.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for l in (doc.get("locations") or []):
            lid = l.get("id")
            if not lid or lid in seen:
                continue
            icon = (l.get("icon_description") or "").strip()
            if not icon:
                continue  # opt-in: only locations with an icon_description get an icon
            seen.add(lid)
            out.append({"id": lid, "name": l.get("name") or lid, "desc": icon})
    return out


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


# --------------------------------------------------------------------------- #
#  Prompt
# --------------------------------------------------------------------------- #
def build_prompt(desc: str, keep_bg: bool, extra: str) -> str:
    subject = desc or "a small old building"
    if keep_bg:
        bg = ("Place the emblem on a small patch of plain aged parchment that fills "
              "the square.\n")
    else:
        bg = ("Draw the emblem on a FLAT, UNIFORM, SOLID PURE CHROMA-KEY GREEN "
              "background (RGB 0,255,0) — no parchment, no texture, no border, no "
              "shadow on the background — so the green can be keyed out to "
              "transparency, leaving only the inked emblem.\n")
    extra_block = f"\nAdditional instructions: {extra}\n" if extra else ""
    return (
        "Create ONE small ICON: a single hand-inked emblem in the style of an old "
        "TOLKIEN fantasy map.\n"
        "\n"
        "STYLE: match the LINEWORK and shading of the provided reference image — "
        "fine aged pen-and-ink / engraving lines, warm sepia-brown ink, subtle "
        "hand-hatching, an antique hand-drawn cartographer's look. Use the reference "
        "ONLY for line style and palette; do NOT copy its map, its parchment "
        "background, its decorative border, or any of its text.\n"
        "\n"
        "SUBJECT — draw a single iconic little motif representing this place, inspired "
        f"by its description: \"{subject}\". Distil it to ONE clear, recognisable "
        "emblem (a building, structure or natural feature), centred and filling most "
        "of the square with a little margin, drawn as a compact map-icon (think the "
        "tiny towers, houses and trees on an old map), NOT a full scene.\n"
        "\n"
        "CRITICAL — NO TEXT AT ALL: absolutely no words, letters, numbers, labels, "
        "captions, signatures or borders anywhere. Treat any text as a mistake.\n"
        "\n"
        f"{bg}"
        "Square 1:1 composition, one emblem only, drawn exactly once.\n"
        f"{extra_block}"
    )


# --------------------------------------------------------------------------- #
#  Image finishing: chroma-key the green background to transparent
# --------------------------------------------------------------------------- #
def key_background_to_alpha(Image, im, bg_thresh: int = 60, feather: float = 0.8):
    """Make the flat background TRANSPARENT, keeping the centred emblem opaque.

    The model paints the emblem on one flat colour (asked for green, often a muted
    olive) that touches every border, while the emblem sits centred with a margin.
    So we flood-fill inward from the corners/edges, keying out whatever that flat
    colour is — robust to its exact hue. Interior detail of the emblem (which never
    touches a border) stays opaque. Returns RGBA. Uses Pillow's C flood fill (fast)."""
    from PIL import ImageDraw, ImageChops, ImageFilter
    work = im.convert("RGB")
    w, h = work.size
    sentinel = (255, 0, 255)  # pure magenta never occurs in sepia ink art
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
             (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    for s in seeds:
        if work.getpixel(s) != sentinel:  # don't reseed an already-filled corner
            ImageDraw.floodfill(work, s, sentinel, thresh=bg_thresh)
    diff = ImageChops.difference(work, Image.new("RGB", work.size, sentinel)).convert("L")
    alpha = diff.point(lambda p: 0 if p == 0 else 255)
    # Suppress any green/olive spill left on the emblem's anti-aliased edge.
    r, g, b = im.convert("RGB").split()
    g2 = ImageChops.darker(g, ImageChops.lighter(r, b))   # G <= max(R,B)
    out = Image.merge("RGB", (r, g2, b)).convert("RGBA")
    if feather and feather > 0:
        alpha = alpha.filter(ImageFilter.GaussianBlur(feather))
    out.putalpha(alpha)
    return out


def finish_icon(Image, data: bytes, size: int, keep_bg: bool, bg_thresh: int):
    """Decode, square-crop to centre, resize to size x size, key bg unless keep_bg."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    if w != h:  # centre-crop to a square just in case the model drifts off 1:1
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    if size and im.size[0] != size:
        im = im.resize((size, size), Image.LANCZOS)
    return im if keep_bg else key_background_to_alpha(Image, im, bg_thresh)


def load_sdk():
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


def make_config(types):
    """Ask for a square (1:1) image back; degrade gracefully for older SDKs."""
    try:
        return types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="1:1"),
        )
    except (TypeError, AttributeError):
        try:
            return types.GenerateContentConfig(response_modalities=["IMAGE"])
        except (TypeError, AttributeError):
            return None


def generate_one(client, types, Image, model, prompt, style_img, size, keep_bg,
                 bg_thresh, out_path: Path) -> tuple[bool, str | None]:
    kwargs = {"model": model, "contents": [prompt, style_img]}
    config = make_config(types)
    if config is not None:
        kwargs["config"] = config
    try:
        resp = client.models.generate_content(**kwargs)
    except Exception as e:
        return False, f"Gemini request failed: {e}"
    notes = []
    for cand in (resp.candidates or []):
        for part in (getattr(getattr(cand, "content", None), "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                im = finish_icon(Image, inline.data, size, keep_bg, bg_thresh)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                im.save(out_path)   # .png keeps alpha when keyed
                return True, None
            if getattr(part, "text", None):
                notes.append(part.text)
    msg = "the model returned no image"
    if notes:
        msg += " (model said: " + " ".join(notes).strip() + ")"
    return False, msg


def render_single(content_dir: Path, loc_id: str, out_path: Path, *, size: int,
                  style_path: Path, model: str, keep_bg: bool, bg_thresh: int,
                  extra: str) -> tuple[bool, str | None]:
    """Render exactly ONE icon, in full isolation: a fresh SDK client and a freshly
    loaded style image, nothing shared with any other icon. Used by the per-icon
    worker subprocess (and reusable on its own)."""
    items = load_locations(content_dir)
    it = next((x for x in items if x["id"] == loc_id), None)
    if it is None:
        return False, f"location '{loc_id}' has no icon_description (or does not exist)"
    api_key = load_api_key()
    if not api_key:
        return False, "no API key (set GEMINI_API_KEY / GOOGLE_API_KEY)"
    try:
        genai, types, Image = load_sdk()
    except RuntimeError as e:
        return False, str(e)
    style_img = Image.open(style_path)           # fresh per process
    client = genai.Client(api_key=api_key)       # fresh per process
    prompt = build_prompt(it["desc"], keep_bg, extra)
    return generate_one(client, types, Image, model, prompt, style_img,
                        size, keep_bg, bg_thresh, out_path)


def spawn_worker(loc_id: str, out_path: Path, args) -> tuple[bool, str | None]:
    """Run render_single in a brand-new Python process so each icon is generated with
    ZERO shared in-memory state — no carry-over between icons."""
    cmd = [sys.executable, str(Path(__file__).resolve()),
           "--worker-id", loc_id, "--worker-out", str(out_path),
           "--size", str(args.size), "--style", args.style,
           "--bg-thresh", str(args.bg_thresh), "--model", args.model]
    if args.keep_bg:
        cmd.append("--keep-bg")
    if args.extra.strip():
        cmd += ["--extra", args.extra.strip()]
    if args.content:
        cmd += ["--content", args.content]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode == 0 and out_path.exists():
        return True, None
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, (err[-1] if err else f"worker exited {proc.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate one ~400x400 Tolkien-style icon per location (Gemini); "
                    "each icon renders in its own process for full isolation.")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR, help="output directory")
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE, help="square icon size in px")
    ap.add_argument("--style", default=DEFAULT_STYLE, help="style reference image")
    ap.add_argument("--only", default="", help="comma-separated node ids to (re)generate")
    ap.add_argument("--n", type=int, default=1,
                    help="how many variants to make per location (default 1)")
    ap.add_argument("--limit", type=int, default=0, help="process at most N pending variants")
    ap.add_argument("--keep-bg", action="store_true", help="keep the green background")
    ap.add_argument("--bg-thresh", type=int, default=60,
                    help="flood-fill colour tolerance for keying the background (default 60)")
    ap.add_argument("--extra", default="", help="extra prompt instructions")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="image model id")
    ap.add_argument("--content", default=None, help="game-data dir")
    ap.add_argument("--no-isolate", dest="isolate", action="store_false",
                    help="render in one shared process instead of a fresh process per icon")
    ap.add_argument("--dry-run", action="store_true",
                    help="list locations + sample prompt, exit")
    # Internal: render exactly one icon (used by the per-icon worker subprocess).
    ap.add_argument("--worker-id", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--worker-out", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()

    n = max(1, args.n)
    content_dir = find_content_dir(args.content)
    if content_dir is None:
        print("ERROR: game-data dir not found (set GAME_DATA_DIR or pass --content).",
              file=sys.stderr)
        return 2

    # Worker mode: one isolated icon, then exit (no banners, no orchestration).
    if args.worker_id:
        ok, err = render_single(
            content_dir, args.worker_id, Path(args.worker_out), size=args.size,
            style_path=resolve_path(args.style), model=args.model,
            keep_bg=args.keep_bg, bg_thresh=args.bg_thresh, extra=args.extra.strip())
        if not ok:
            print(f"ERROR: {err}", file=sys.stderr)
            return 1
        return 0
    try:
        items = load_locations(content_dir)
    except ImportError:
        print("ERROR: PyYAML missing — install with: pip install pyyaml", file=sys.stderr)
        return 2
    if not items:
        print(f"ERROR: no locations with an `icon_description:` found in {content_dir}. "
              "Add `icon_description: \"...\"` to a location to give it an icon.",
              file=sys.stderr)
        return 2

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if only:
        missing = only - {it["id"] for it in items}
        if missing:
            print(f"WARNING: --only ids not found: {', '.join(sorted(missing))}",
                  file=sys.stderr)
        items = [it for it in items if it["id"] in only]

    out_dir = resolve_path(args.out)

    def out_for(lid: str, v: int) -> Path:
        # one file per location for --n 1; numbered variants for --n>1.
        return out_dir / (f"{lid}.png" if n == 1 else f"{lid}-v{v}.png")

    # Build the work list as (location, variant) jobs. Existing icons are NEVER
    # overwritten — only missing variant files are (re)generated.
    jobs, skipped = [], 0
    for it in items:
        for v in range(1, n + 1):
            out_path = out_for(it["id"], v)
            if out_path.exists():
                skipped += 1
            else:
                jobs.append({"it": it, "v": v, "out": out_path})
    if args.limit and len(jobs) > args.limit:
        jobs = jobs[:args.limit]

    suffix = f" x{n} variants" if n > 1 else ""
    print(f"Icons: {len(items)} location(s){suffix} -> {out_dir}")
    print(f"  to generate: {len(jobs)}   skipped (already exist): {skipped}")
    for it in items:
        print(f"  + {it['id']}: {it['desc'][:70]}")

    if args.dry_run:
        if items:
            print("\n=== SAMPLE PROMPT (" + items[0]["id"] + ") ===")
            print(build_prompt(items[0]["desc"], args.keep_bg, args.extra.strip()))
        print("\n=== MODEL/STYLE/SIZE ===")
        print(f"{args.model} · {resolve_path(args.style)} · {args.size}px square · "
              f"{n} variant(s)/location · "
              f"{'opaque green bg' if args.keep_bg else 'transparent (green keyed)'}")
        return 0

    if not jobs:
        print("Nothing to do — every icon already exists (this skill never "
              "overwrites). Delete an icon to redo it, or use --n N for more variants.")
        return 0

    style_path = resolve_path(args.style)
    if not style_path.exists():
        print(f"ERROR: style image not found: {style_path}\n"
              f"Put your Tolkien style reference there, or pass --style PATH.",
              file=sys.stderr)
        return 2
    api_key = load_api_key()
    if not api_key:
        print("ERROR: no API key. Set GEMINI_API_KEY (or GOOGLE_API_KEY) in your "
              "environment or the repo .env.\nGet a key at "
              "https://aistudio.google.com/apikey", file=sys.stderr)
        return 2
    mode = "isolated process per icon" if args.isolate else "shared process"
    print(f"\nGenerating {len(jobs)} icon(s) ({args.size}px, "
          f"{'opaque' if args.keep_bg else 'transparent'}) with {args.model} "
          f"[{mode}], one at a time ...")

    # Shared-process mode loads the SDK / client / style image once (faster, but the
    # icons share those in-memory objects). Default isolate mode does not.
    client = types = Image = style_img = None
    if not args.isolate:
        try:
            genai, types, Image = load_sdk()
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        style_img = Image.open(style_path)
        client = genai.Client(api_key=api_key)

    ok_n, fail = 0, []
    for i, job in enumerate(jobs, 1):
        it = job["it"]
        label = job["out"].stem
        print(f"  [{i}/{len(jobs)}] {label} ...", flush=True)
        if args.isolate:
            ok, err = spawn_worker(it["id"], job["out"], args)
        else:
            prompt = build_prompt(it["desc"], args.keep_bg, args.extra.strip())
            ok, err = generate_one(client, types, Image, args.model, prompt, style_img,
                                   args.size, args.keep_bg, args.bg_thresh, job["out"])
        if ok:
            ok_n += 1
        else:
            fail.append((label, err))
            print(f"      FAILED: {err}", file=sys.stderr)

    print(f"\nDone: {ok_n} saved -> {out_dir}" + (f", {len(fail)} failed" if fail else ""))
    if fail:
        print("Re-run to retry the failures (existing icons are skipped):")
        for label, _ in fail:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
