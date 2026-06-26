#!/usr/bin/env python3
"""
generateMap — generate ONE big, old, hand-drawn WORLD map of the whole game from
its content YAML, using the Google Gemini image API ("Nano Banana",
gemini-2.5-flash-image) and a style reference image.

It reads every node marked `type: location` from the game data, groups them by
world cell, and asks the model to draw:
  * each town/village cell as a CLUSTER of distinct iconic buildings — one building
    per location, informed by that location's description;
  * each wilderness cell (e.g. "Skåne Woods") as natural scenery (forest / feature),
    NOT a town;
positioned across the map using each cell's and each location's SAVED map
coordinates.

The map carries NO text at all (no title, no labels), its edges fade to transparent
(alpha), and it renders at ~2K resolution.

Usage (from the repo root):

  python .claude/skills/generateMap/generate_map.py
  python .claude/skills/generateMap/generate_map.py --out maps/output --size 2048

Options:
  --out PATH       output file or directory (default maps/output/world.png; a
                   directory gets world.png). The world map OVERWRITES on re-run.
  --size N         long-edge resolution in px (default 2048, i.e. ~2K).
  --feather F      fraction of each side that fades to transparent (default 0.06;
                   0 disables).
  --aspect RATIO   output aspect ratio (default 4:3).
  --style PATH     style reference image (default maps/input/style/style.png).
  --extra TEXT     extra free-text instructions appended to the prompt.
  --model NAME     override the image model (default gemini-2.5-flash-image).
  --content DIR    game-data dir (default $GAME_DATA_DIR -> $CONTENT_DIR ->
                   ../OneLife-KBK-mystery).
  --dry-run        print the composed prompt + plan and exit (no API call).

Requirements: pip install google-genai pillow pyyaml
API key: set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment or the repo .env.
Get one at https://aistudio.google.com/apikey
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
DEFAULT_SIZE = 2048          # ~2K on the long edge
DEFAULT_FEATHER = 0.06       # fraction of each side that fades to transparent
# Aspect ratios the model accepts (others are rejected by the API).
VALID_ASPECTS = {
    "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9",
}


def repo_root() -> Path:
    # .claude/skills/generateMap/generate_map.py -> repo root is 3 parents up.
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


def load_content(content_dir: Path) -> tuple[list, dict, list, list]:
    """Merge every *.yaml in the data repo. Return (cells, locations_by_id, nodes,
    edges) — edges normalized to {from, to, text} (text = label + log, for rail/road
    classification). Inline node edges get their `from` from the owning node."""
    import yaml  # optional dep; caller guards the ImportError
    cells: list = []
    locs: dict = {}
    nodes: list = []
    edges: list = []

    def _edge(e: dict, from_id: str | None) -> dict:
        log = (e.get("effects") or {}).get("log", "")
        return {"from": e.get("from") or from_id, "to": e.get("to"),
                "text": f"{e.get('label', '')} {log}"}

    for f in sorted(content_dir.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        cells += doc.get("cells") or []
        for n in (doc.get("nodes") or []):
            nodes.append(n)
            for e in (n.get("edges") or []):
                edges.append(_edge(e, n.get("id")))
        for e in (doc.get("edges") or []):
            edges.append(_edge(e, None))
        for l in (doc.get("locations") or []):
            locs[l.get("id")] = l
    return cells, locs, nodes, edges


# --------------------------------------------------------------------------- #
#  World model: cluster location nodes by cell, position them, classify scenery
# --------------------------------------------------------------------------- #
_WILD_KINDS = {"wilderness", "wild", "nature", "forest", "woods"}
_WILD_WORDS = ("wood", "forest", "wild", "marsh", "moor", "heath", "fen", "fell",
               "countryside", "coast", "shore", "lake", "river", "mountain", "field")


def _is_wilderness(cell: dict) -> bool:
    """A cell is wilderness if its kind says so, or its name/region reads natural
    (e.g. 'Skåne Woods'). Everything else is drawn as a town/city cluster."""
    if _norm(cell.get("kind")) in _WILD_KINDS:
        return True
    text = _norm(cell.get("name")) + " " + _norm(cell.get("region"))
    return any(w in text for w in _WILD_WORDS)


def _cell_pos(cell: dict, gx_range: tuple, gy_range: tuple) -> tuple[float, float]:
    """Normalized (x,y) for a cell: its saved map coords, else its grid position
    padded into the 0.15..0.85 band so nothing sits in the frame's edge."""
    m = cell.get("map") or {}
    if m.get("x") is not None and m.get("y") is not None:
        return float(m["x"]), float(m["y"])
    (gxmin, gxmax), (gymin, gymax) = gx_range, gy_range

    def lerp(v, lo, hi):
        return 0.5 if hi == lo else 0.15 + 0.70 * (v - lo) / (hi - lo)

    return (lerp(cell.get("grid_x", 0), gxmin, gxmax),
            lerp(cell.get("grid_y", 0), gymin, gymax))


def build_clusters(cells: list, locs: dict, nodes: list) -> list:
    """Group `type: location` nodes by cell, attaching each cell's global position
    (from saved coords / grid), wilderness flag, and per-location descriptions +
    in-cell coords. Returns clusters sorted top-to-bottom, left-to-right."""
    gxs = [c.get("grid_x") for c in cells if c.get("grid_x") is not None]
    gys = [c.get("grid_y") for c in cells if c.get("grid_y") is not None]
    gx_range = (min(gxs), max(gxs)) if gxs else (0, 1)
    gy_range = (min(gys), max(gys)) if gys else (0, 1)
    cell_by_id = {c.get("id"): c for c in cells}

    feats_by_cell: dict = {}
    for n in nodes:
        if _norm(n.get("type")) != "location":
            continue
        loc = locs.get(n.get("location")) or {}
        cell_id = loc.get("cell") or n.get("cell")
        desc = (loc.get("description") or n.get("body") or n.get("title") or "").strip()
        m = n.get("map") or {}
        feats_by_cell.setdefault(cell_id, []).append({
            "id": n.get("id"), "title": n.get("title") or n.get("id"), "desc": desc,
            "nx": (float(m["x"]) if m.get("x") is not None else None),
            "ny": (float(m["y"]) if m.get("y") is not None else None),
        })

    clusters = []
    for cell_id, feats in feats_by_cell.items():
        cell = cell_by_id.get(cell_id, {"id": cell_id})
        cx, cy = _cell_pos(cell, gx_range, gy_range)
        # Classify the cluster: wilderness if the cell reads natural; otherwise a
        # village when it's only a couple of places (<=3 nodes), else a city.
        if _is_wilderness(cell):
            settlement = "wilderness"
        elif len(feats) <= 3:
            settlement = "village"
        else:
            settlement = "city"
        clusters.append({
            "cell": cell_id, "kind": _norm(cell.get("kind")) or "town",
            "wilderness": settlement == "wilderness", "settlement": settlement,
            "x": cx, "y": cy, "feats": feats,
        })
    clusters.sort(key=lambda c: (round(c["y"], 2), round(c["x"], 2)))
    return clusters


_RAIL_WORDS = ("train", "rail", "railway", "railroad", " line")


def build_connections(clusters: list, edges: list) -> list:
    """Routes to draw between clusters: a ROAD between each settlement and its
    nearest neighbours (so the towns/village are all linked), a TRACK to any
    wilderness the content actually connects to, and a RAILROAD between cities that
    a rail edge joins. Returns [{kind, a, b}] with a/b the two clusters."""
    by_cell = {c["cell"]: c for c in clusters}
    node_cluster = {f["id"]: c for c in clusters for f in c["feats"]}

    # inter-cluster edges in the content, flagged rail vs road by their wording.
    edge_pairs: dict = {}
    for e in edges:
        ca, cb = node_cluster.get(e.get("from")), node_cluster.get(e.get("to"))
        if ca is None or cb is None or ca is cb:
            continue
        key = frozenset((ca["cell"], cb["cell"]))
        rail = any(w in (e.get("text") or "").lower() for w in _RAIL_WORDS)
        edge_pairs.setdefault(key, {"rail": False})["rail"] |= rail

    settlements = [c for c in clusters if not c["wilderness"]]

    def dist(a, b):
        return ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5

    road_keys = set()
    # link each settlement to its (up to) two nearest settlements
    for a in settlements:
        nearest = sorted((s for s in settlements if s is not a), key=lambda s: dist(a, s))
        for b in nearest[:2]:
            road_keys.add(frozenset((a["cell"], b["cell"])))
    # a track to any wilderness the content links to a settlement
    for key in edge_pairs:
        if any(by_cell[c]["wilderness"] for c in key if c in by_cell):
            road_keys.add(key)

    # a railroad between two cities joined by a rail edge
    rail_keys = set()
    for key, info in edge_pairs.items():
        cs = [by_cell[c] for c in key if c in by_cell]
        if info["rail"] and len(cs) == 2 and all(c["settlement"] == "city" for c in cs):
            rail_keys.add(key)

    def pair(key):
        cs = [by_cell[c] for c in key if c in by_cell]
        return cs if len(cs) == 2 else None

    conns = []
    for key in rail_keys:
        cs = pair(key)
        if cs:
            conns.append({"kind": "railroad", "a": cs[0], "b": cs[1]})
    for key in road_keys:
        cs = pair(key)
        if cs:
            wild = any(c["wilderness"] for c in cs)
            conns.append({"kind": "track" if wild else "road", "a": cs[0], "b": cs[1]})
    return conns


# --------------------------------------------------------------------------- #
#  Prompt
# --------------------------------------------------------------------------- #
def _pct(v: float) -> str:
    return f"{round(max(0.0, min(1.0, v)) * 100)}%"


def _intra(nx, ny) -> str:
    """A coarse within-cluster direction from a location's saved in-cell coords."""
    if nx is None or ny is None:
        return ""
    h = "west" if nx < 0.4 else "east" if nx > 0.6 else ""
    v = "north" if ny < 0.4 else "south" if ny > 0.6 else ""
    return f" (toward the {(v + h) or 'centre'} of the cluster)"


def build_prompt(clusters: list, connections: list, aspect: str, extra: str) -> str:
    n_feats = sum(len(c["feats"]) for c in clusters)
    lines = []
    for c in clusters:
        pos = f"around {_pct(c['x'])} across and {_pct(c['y'])} down"
        if c["wilderness"]:
            lines.append(
                f"- WILDERNESS {pos}: NOT a town — draw open natural scenery here "
                f"(forest / woods / wild countryside) with {len(c['feats'])} natural "
                "feature(s):")
        else:
            size = "small VILLAGE" if c["settlement"] == "village" else "CITY / TOWN"
            lines.append(
                f"- A {size} {pos}: a tight CLUSTER of {len(c['feats'])} distinct old "
                "buildings drawn close together as one settlement, one building per "
                "place below:")
        for f in c["feats"]:
            lines.append(f"    • a building/feature that is: {f['desc']}{_intra(f['nx'], f['ny'])}")
    layout = "\n".join(lines)

    conn_lines = []
    for cn in connections:
        a = f"{_pct(cn['a']['x'])},{_pct(cn['a']['y'])}"
        b = f"{_pct(cn['b']['x'])},{_pct(cn['b']['y'])}"
        if cn["kind"] == "railroad":
            conn_lines.append(f"    • a RAILWAY LINE (a clear railroad track with "
                              f"sleepers/ties) running between {a} and {b}")
        elif cn["kind"] == "track":
            conn_lines.append(f"    • a small dirt ROAD / TRACK between {a} and {b}")
        else:
            conn_lines.append(f"    • a ROAD between {a} and {b}")
    conn_block = ""
    if conn_lines:
        conn_block = (
            "\nCONNECTIONS — draw these routes (and only these) winding across the "
            "open countryside between the settlements; keep each a single clear line:\n"
            + "\n".join(conn_lines) + "\n")

    extra_block = f"\nAdditional instructions: {extra}\n" if extra else ""

    return (
        "Create ONE big, old, hand-drawn map of a whole region in a single picture.\n"
        "\n"
        "CRITICAL — NO TEXT AT ALL: This map must contain absolutely NO written words "
        "anywhere — no title, no place names, no building labels, no street names, no "
        "captions, no legend/key, no compass letters (N/S/E/W), no numbers, no scale, "
        "no signatures — nothing. If the style reference image contains any labels or "
        "writing, DO NOT copy them; leave every building, road and landmark completely "
        "unlabelled. Treat ANY text as a mistake.\n"
        "\n"
        "STYLE: Match the look and feel of the provided reference image — use it as "
        "the art-style guide for line work, palette, texture and overall mood ONLY "
        "(not its text). The map must look OLD, WEATHERED and WORN: aged/foxed paper "
        "or parchment, faded and stained — like a treasured old document, not a clean "
        "modern print.\n"
        "\n"
        "It must look HAND-DRAWN and HAND-INKED, never printed, vector or digital: "
        "visible, slightly irregular pen strokes, hand-shaded hills and water, "
        "organic wobble to coastlines, roads and borders.\n"
        "\n"
        "ZOOMED OUT: this is a wide REGIONAL map. Draw each settlement as a SMALL, "
        "separate island of buildings no wider than about a fifth of the map, sitting "
        "FAR APART from the others, with large EMPTY stretches of open countryside, "
        "fields, forest and water filling the big gaps between them. At least half of "
        "the whole map must be open, building-free land. The cities and the village "
        "must be clearly SEPARATE places — do NOT let their clusters touch or merge "
        "into one continuous built-up area.\n"
        "\n"
        f"DRAW {n_feats} PLACES, grouped into the settlements and wilderness listed "
        "below. Positions are fractions of the map (0% = left/top, 100% = "
        "right/bottom); keep each group near its position and keep the groups clearly "
        "SEPARATED by open country:\n"
        f"{layout}\n"
        f"{conn_block}"
        "\n"
        "Each building or feature is a PICTURE ONLY — visually distinct and "
        "recognisable from its description, drawn exactly once, with NO name or "
        "caption beside it. Use the descriptions ONLY to decide what to draw; never "
        "write any of those words on the map.\n"
        "\n"
        "Draw these landmark buildings and features LARGE, bold and eye-catching, "
        "like an old PICTORIAL map where the important places loom oversized over the "
        "land. Their scale need NOT be consistent with each other or with the rest of "
        "the map — exaggerate them so each is easy to pick out.\n"
        "\n"
        "Fill the frame edge to edge with map. Draw NO rectangular border, frame or "
        "cartouche; let the artwork fade softly toward the very edges.\n"
        f"Output the map in a {aspect} aspect ratio.\n"
        f"{extra_block}"
    )


# --------------------------------------------------------------------------- #
#  Image: key, SDK, generation, finishing (resize + transparent feathered edges)
# --------------------------------------------------------------------------- #
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


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (repo_root() / path)


def pick_out_path(out_arg: str) -> Path:
    """The world map is a single canonical file that OVERWRITES on re-run."""
    out = resolve_path(out_arg)
    if out.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        out.parent.mkdir(parents=True, exist_ok=True)
        return out
    out.mkdir(parents=True, exist_ok=True)
    return out / "world.png"


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


def _edge_mask(Image, size: tuple, frac: float):
    """An alpha mask: opaque in the middle, fading linearly to 0 over `frac` of each
    side, so the rendered map's edges become transparent."""
    from PIL import ImageChops
    w, h = size
    mx, my = max(1, int(w * frac)), max(1, int(h * frac))

    def ramp(length, margin):
        out = []
        for i in range(length):
            d = min(i, length - 1 - i)
            out.append(255 if d >= margin else int(255 * d / margin))
        return out

    hrow = Image.new("L", (w, 1)); hrow.putdata(ramp(w, mx)); hmask = hrow.resize((w, h))
    vcol = Image.new("L", (1, h)); vcol.putdata(ramp(h, my)); vmask = vcol.resize((w, h))
    return ImageChops.multiply(hmask, vmask)


def finish_image(Image, data: bytes, size: int, feather: float):
    """Decode the model's image, resize so the long edge is ~`size`, and feather the
    edges to transparent. Returns an RGBA Image."""
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = im.size
    if size and max(w, h) != size:
        scale = size / float(max(w, h))
        im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    if feather and feather > 0:
        im.putalpha(_edge_mask(Image, im.size, feather))
    return im


def generate_and_save(client, types, Image, model, prompt, style_img, aspect,
                      out_path: Path, size: int, feather: float) -> tuple[bool, str | None]:
    """One Gemini call -> finish -> save PNG (with alpha). Returns (ok, error)."""
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
                im = finish_image(Image, inline.data, size, feather)
                im.save(out_path)   # .png keeps the alpha (transparent edges)
                return True, None
            if getattr(part, "text", None):
                notes.append(part.text)
    msg = "the model returned no image"
    if notes:
        msg += " (model said: " + " ".join(notes).strip() + ")"
    return False, msg


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate one big hand-drawn WORLD map (all location nodes) via Gemini.")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR, help="output file or directory")
    ap.add_argument("--size", type=int, default=DEFAULT_SIZE,
                    help="long-edge resolution in px (default 2048, ~2K)")
    ap.add_argument("--feather", type=float, default=DEFAULT_FEATHER,
                    help="fraction of each side that fades to transparent (0 disables)")
    ap.add_argument("--aspect", default="4:3", help="output aspect ratio")
    ap.add_argument("--style", default=DEFAULT_STYLE, help="style reference image")
    ap.add_argument("--extra", default="", help="extra prompt instructions")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="image model id")
    ap.add_argument("--content", default=None,
                    help="game-data dir; default $GAME_DATA_DIR -> $CONTENT_DIR "
                         "-> ../OneLife-KBK-mystery")
    ap.add_argument("--dry-run", action="store_true", help="print plan/prompt and exit")
    args = ap.parse_args()

    aspect = args.aspect.strip()
    if aspect not in VALID_ASPECTS:
        print(f"ERROR: aspect '{aspect}' not supported. Choose one of: "
              f"{', '.join(sorted(VALID_ASPECTS))}", file=sys.stderr)
        return 2

    content_dir = find_content_dir(args.content)
    if content_dir is None:
        print("ERROR: game-data dir not found (set GAME_DATA_DIR or pass --content).",
              file=sys.stderr)
        return 2
    try:
        cells, locs, nodes, edges = load_content(content_dir)
    except ImportError:
        print("ERROR: PyYAML missing — install with: pip install pyyaml", file=sys.stderr)
        return 2

    clusters = build_clusters(cells, locs, nodes)
    n_feats = sum(len(c["feats"]) for c in clusters)
    if n_feats == 0:
        print(f"ERROR: no `type: location` nodes found in {content_dir}.", file=sys.stderr)
        return 2
    connections = build_connections(clusters, edges)
    prompt = build_prompt(clusters, connections, aspect, args.extra.strip())
    out_path = pick_out_path(args.out)

    print(f"World map: {n_feats} location(s) in {len(clusters)} cluster(s) "
          f"({sum(1 for c in clusters if not c['wilderness'])} town/city, "
          f"{sum(1 for c in clusters if c['wilderness'])} wilderness) -> {out_path.name}")
    for c in clusters:
        print(f"  - {c['cell']} [{c['settlement']}] @ {_pct(c['x'])},{_pct(c['y'])}: "
              f"{len(c['feats'])} place(s)")
    for cn in connections:
        print(f"  ~ {cn['kind']}: {cn['a']['cell']} <-> {cn['b']['cell']}")

    if args.dry_run:
        print("\n=== MODEL ===\n" + args.model)
        print(f"=== SIZE/ASPECT/FEATHER ===\n{args.size}px long edge · {aspect} · feather {args.feather}")
        print("=== STYLE IMAGE ===\n" + str(resolve_path(args.style)))
        print("=== OUT ===\n" + str(out_path))
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
    print(f"\nGenerating world map ({aspect}, ~{args.size}px) with {args.model} ...")
    ok, err = generate_and_save(client, types, Image, args.model, prompt, style_img,
                                aspect, out_path, args.size, args.feather)
    if not ok:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"\nSaved world map -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
