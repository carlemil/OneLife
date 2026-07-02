"""LLM translation generator for a game's authored text.

    python -m app.translate --game SSandby1992 --lang sv
    python -m app.translate --game SSandby1992 --lang sv --only story_nodes --overwrite
    python -m app.translate --game SSandby1992 --lang sv --check

Reads the English base content (games/<id>/data/*.yaml), asks Claude to translate every
translatable leaf (i18n.iter_leaves — the single source of truth for what's translatable)
into the target language, and writes a shape-mirroring sidecar games/<id>/i18n/<lang>.yaml
that content.seed_translations then loads. English stays the source of truth; a leaf with
no translation simply falls back to English at render time.

Build-time + server-side: it constructs its OWN Anthropic client from ANTHROPIC_API_KEY
(independent of the runtime LLM_PROVIDER, which may be 'browser'). With no key it falls
back to an identity copy so the pipeline is still exercisable.

Rules enforced on the model (system prompt) + verified in code:
  - translate prose into the target language;
  - preserve verbatim: the ▒ glyph runs, {placeholders}, markdown *marks*, \\n, numbers,
    and proper names (people/places/bands). Role descriptors ("The Janitor") ARE translated.
  - never touch ids / flags / conditions / crossword grids (those aren't in the leaf set).
For solution answer-sets, emit the natural forms a target-language player would type.
"""
import os
import re
import sys
import json
import argparse
import asyncio

import yaml

from . import content, gamestate, i18n

_MODEL = os.environ.get("TRANSLATE_MODEL") or os.environ.get("ACTOR_MODEL", "claude-haiku-4-5")
_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# Human-readable language names for the prompt (fallback: the code itself).
_LANG_NAMES = {
    "sv": "Swedish", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
    "nl": "Dutch", "da": "Danish", "no": "Norwegian", "nb": "Norwegian Bokmål",
    "fi": "Finnish", "pt": "Portuguese", "pl": "Polish", "ja": "Japanese",
}

_PLACEHOLDER = re.compile(r"\{[^}]+\}")


def _client():
    if not _KEY:
        return None
    try:
        import anthropic
        return anthropic.AsyncAnthropic(api_key=_KEY)
    except Exception as e:  # noqa: BLE001
        print(f"[translate] anthropic client unavailable ({e}); using identity fallback")
        return None


def _parity_ok(src: str, dst: str) -> bool:
    """Reject a translation that dropped/added a {placeholder} or changed the ▒ glyph count
    — those carry meaning and must survive verbatim."""
    if src.count("▒") != dst.count("▒"):
        return False
    if set(_PLACEHOLDER.findall(src)) != set(_PLACEHOLDER.findall(dst)):
        return False
    return True


def _glossary(data: dict) -> list[str]:
    """Proper nouns to keep EXACT across the whole game (names, places), for consistency."""
    terms: set[str] = set()
    for c in data.get("characters", []) or []:
        if c.get("reveal_name"):
            terms.add(c["reveal_name"])
    for cell in data.get("cells", []) or []:
        if cell.get("name"):
            terms.add(cell["name"])
    return sorted(terms)


def _system(lang_name: str, glossary: list[str]) -> str:
    gloss = ("\nKEEP THESE PROPER NOUNS EXACT (names of people, places, bands): "
             + ", ".join(glossary)) if glossary else ""
    return (
        f"You are a professional game-localization translator. Translate the given strings "
        f"from English into {lang_name}, for a narrative text-adventure. Return a natural, "
        f"idiomatic, in-tone translation — not a literal word-for-word one.\n"
        f"HARD RULES:\n"
        f"- Preserve EXACTLY, without translating or reordering: the block glyph ▒ and any "
        f"run of it, {{curly placeholders}}, markdown *emphasis* marks, literal \\n, and all "
        f"numbers/dates/codes.\n"
        f"- Keep proper names of people, places and bands unchanged. TRANSLATE only generic "
        f"role descriptors (e.g. 'The Janitor').\n"
        f"- For a player's puzzle answer, give the natural word(s) a {lang_name} player would "
        f"type for the same concept.\n"
        f"- Do not add quotes, notes, or commentary." + gloss + "\n"
        f"INPUT is a JSON object mapping ids to English strings. OUTPUT ONLY a JSON object "
        f"with the SAME ids mapping to the {lang_name} translations. No other text."
    )


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        text = text[a:b + 1]
    return json.loads(text)


async def _translate_batch(client, system: str, items: dict[str, str]) -> dict[str, str]:
    """items: {id: english}. Returns {id: translated}. Identity copy if no client."""
    if client is None:
        return dict(items)
    msg = [{"role": "user", "content": json.dumps(items, ensure_ascii=False)}]
    for attempt in range(2):
        try:
            resp = await client.messages.create(
                model=_MODEL, max_tokens=8000, system=system, messages=msg)
            out = _parse_json(resp.content[0].text)
            return {k: str(out.get(k, items[k])) for k in items}
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                print(f"[translate] batch failed ({e}); keeping English for {len(items)} leaf(s)")
                return dict(items)
    return dict(items)


def _chunks(leaves: list[tuple], max_items=40, max_chars=6000):
    batch, size = [], 0
    for lf in leaves:
        t = lf[3]
        if batch and (len(batch) >= max_items or size + len(t) > max_chars):
            yield batch
            batch, size = [], 0
        batch.append(lf)
        size += len(t)
    if batch:
        yield batch


async def run(game_id: str, lang: str, only=None, overwrite=False, check=False) -> int:
    data, _ = content.load_dir(gamestate.data_dir_for(game_id))
    meta = data.get("game", {}) or {}
    leaves = list(i18n.iter_leaves(game_id, data, meta))
    if only:
        leaves = [lf for lf in leaves if lf[0] in only]

    # Existing sidecar (for incremental fill / preserving hand-edits).
    path = i18n.sidecar_path(game_id, lang)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                existing = yaml.safe_load(fh) or {}
        except Exception as e:  # noqa: BLE001
            print(f"[translate] could not read existing {path}: {e}")

    def cur(etype, eid, p):
        ent = i18n._sidecar_entity(existing, etype, game_id if etype == "game" else eid)
        return i18n.get_path(ent, p) if ent else None

    todo = [lf for lf in leaves
            if overwrite or not (isinstance(cur(lf[0], lf[1], lf[2]), str)
                                 and cur(lf[0], lf[1], lf[2]).strip())]
    have = len(leaves) - len(todo)

    if check:
        print(f"[{game_id}/{lang}] {len(leaves)} translatable leaf(s): "
              f"{have} present, {len(todo)} missing.")
        for etype, eid, p, _t in todo[:50]:
            print(f"  missing: {etype} {eid} {p}")
        if len(todo) > 50:
            print(f"  … and {len(todo) - 50} more")
        return 0

    lang_name = _LANG_NAMES.get(lang, _LANG_NAMES.get(lang.split("-")[0], lang))
    client = _client()
    system = _system(lang_name, _glossary(data))
    if client is None:
        print(f"[translate] no ANTHROPIC_API_KEY — writing identity (English) copies for {len(todo)} leaf(s)")

    # Translate per entity_type batch (keeps like content together, bounds token use).
    by_type: dict = {}
    for lf in todo:
        by_type.setdefault(lf[0], []).append(lf)

    sidecar = existing if existing else {}
    sidecar.setdefault("lang", lang)
    kept_english = 0
    for etype, group in by_type.items():
        for batch in _chunks(group):
            items = {str(i): lf[3] for i, lf in enumerate(batch)}
            out = await _translate_batch(client, system, items)
            for i, lf in enumerate(batch):
                etype_, eid, p, src = lf
                dst = out.get(str(i), src)
                if client is not None and not _parity_ok(src, dst):
                    kept_english += 1
                    dst = src   # formatting-mark mismatch → keep English, safe fallback
                # Write into the shape-mirroring sidecar under its top-level key.
                top = i18n.FLATTENERS[etype_][0]
                if etype_ == "game":
                    sidecar.setdefault("game", {})
                    i18n.set_path(sidecar["game"], p, dst)
                else:
                    sidecar.setdefault(top, {}).setdefault(eid, {})
                    i18n.set_path(sidecar[top][eid], p, dst)
            print(f"  [{etype}] translated {len(batch)} leaf(s)")

    os.makedirs(i18n.i18n_dir(game_id), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(sidecar, fh, allow_unicode=True, sort_keys=True, width=100)
    print(f"[translate] wrote {path} — {len(todo)} translated, {have} kept, "
          f"{kept_english} reverted to English (parity).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Translate a game's authored text via Claude.")
    ap.add_argument("--game", required=True)
    ap.add_argument("--lang", required=True, help="BCP-47 target code, e.g. sv")
    ap.add_argument("--only", action="append", help="restrict to an entity_type (repeatable)")
    ap.add_argument("--overwrite", action="store_true", help="retranslate even present leaves")
    ap.add_argument("--check", action="store_true", help="report coverage, write nothing")
    args = ap.parse_args(argv)
    if not gamestate.is_valid_game(args.game):
        print(f"ERROR: no such game '{args.game}'", file=sys.stderr)
        return 2
    return asyncio.run(run(args.game, args.lang, only=args.only,
                           overwrite=args.overwrite, check=args.check))


if __name__ == "__main__":
    sys.exit(main())
