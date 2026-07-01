#!/usr/bin/env python3
"""Scaffold a new OneLife game under games/<id>/data/.

Writes a MINIMAL, lint-valid, end-to-end playable starter — organized by-type exactly
like games/SandbyMystery/data/ — with a working example of every mechanic (a cell, a
location, an NPC + AI dialogue gate, a puzzle + clue, a death node, an ending) and a full
`game:` block (title/subtitle/first_summary + per-game engine config). The output is
guaranteed to pass `make lint` so it seeds and appears in the lobby immediately; theme it
afterwards by editing the YAML.

  python .claude/skills/initGame/init_game.py --id MyGame [--title "My Game"] \
      [--subtitle "..."] [--first-summary "You wake."] \
      [--region "..."] [--period "..."] [--force] [--dry-run]

Pure stdlib — no pyyaml, no DB, no running stack. See .claude/skills/initGame/SKILL.md.
"""
import argparse
import re
import sys
from pathlib import Path

# .claude/skills/initGame/init_game.py -> repo root is 3 levels up.
REPO_ROOT = Path(__file__).resolve().parents[3]


def yq(s: str) -> str:
    """YAML double-quoted scalar (safe for empty strings and punctuation)."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- #
#  Static template files (no substitution) — house style, one file per kind.
# --------------------------------------------------------------------------- #
STATIC: dict[str, str] = {}

STATIC["arcs.yaml"] = """\
# Narrative threads. `main` is the spine (is_spine: true).
arcs:
  - id: main
    title: Main
    is_spine: true
"""

STATIC["locations.yaml"] = """\
# Places within cells. Node `location:` fields reference these ids.
locations:
  - id: plaza
    name: The Village Square
    description: A quiet square with a stone well at its centre and a few shuttered houses.
    cell: home
"""

STATIC["characters.yaml"] = """\
# NPCs. `persona` is the second-person brief the AI Actor reads in character.
# Optional `reveal_name` is the true name revealed in play when the public name withholds it.
characters:
  - id: elder
    name: the Elder
    persona: >
      You are a wary old villager who has seen too much. You speak in short, careful
      sentences and do not trust strangers easily — but genuine warmth or patience wins
      you over. You know the way onward, and share it only once you feel the newcomer can
      be trusted.
"""

STATIC["nodes-narration.yaml"] = """\
# Narration beats. Exactly ONE node across all files is the entry (`entry: true`).
nodes:
  - id: start
    arc: main
    type: narration
    location: plaza
    title: "You Wake"
    entry: true
    media: {image_theme: village-square-dawn, music_theme: uneasy-quiet}
    body: >
      You come to on cold cobblestones. A village square, empty and grey — you do not
      remember how you got here. A stone well stands at the centre, and an old figure
      watches you from a doorway.
    edges:
      - id: e-start-square
        to: square
        label: Get to your feet
        effects: {progress_points: 5, log: "You got to your feet."}
"""

STATIC["nodes-location.yaml"] = """\
# Location (hub) nodes. `world_access: true` marks a travel hub (opens the world map).
nodes:
  - id: square
    arc: main
    type: location
    location: plaza
    title: "The Village Square"
    world_access: true
    media: {image_theme: village-square-dawn, music_theme: uneasy-quiet}
    body: >
      The square is still. The well waits at its centre; the old villager lingers in a
      doorway. A low arch leads out of the village.
    edges:
      - id: e-square-elder
        to: elder
        label: Approach the old villager
        effects: {log: "You approach the villager."}
      - id: e-square-well
        to: well
        label: Examine the well
        effects: {log: "You lean over the well."}
      # The way out opens once you've won the villager over OR answered the well's riddle
      # (demonstrates conditions — the engine hides this edge until one path is done).
      - id: e-square-out
        to: gate-out
        label: Leave through the arch
        conditions: {any: [{flag_set: met_elder}, {puzzle_solved: well-riddle}]}
        effects: {progress_points: 20, log: "You walk out through the arch."}
      # A tempting, deadly shortcut (danger foreshadows the death; rollback is the escape).
      - id: e-square-peril
        to: peril
        label: Force the rusted gate
        danger: 2
        effects: {log: "You throw yourself at the rusted gate."}
"""

STATIC["nodes-gate.yaml"] = """\
# Gate nodes host an AI-dialogue encounter (`gate:` -> gates.yaml). The player leaves
# through an ordinary edge when ready.
nodes:
  - id: elder
    arc: main
    type: gate
    location: plaza
    gate: elder-greeting
    title: "The Old Villager"
    media: {image_theme: villager-doorway, music_theme: wary-quiet}
    body: >
      The old villager watches you with guarded eyes, saying nothing yet.
    edges:
      - id: e-elder-square
        to: square
        label: Step back to the square
        sort_order: 9
        effects: {log: "You step back."}
"""

STATIC["nodes-puzzle.yaml"] = """\
# Puzzle nodes host a puzzle (`puzzle:` -> puzzles.yaml).
nodes:
  - id: well
    arc: main
    type: puzzle
    location: plaza
    puzzle: well-riddle
    title: "The Stone Well"
    media: {image_theme: stone-well, music_theme: wary-quiet}
    body: >
      Words are chiselled around the well's rim — a riddle, worn but still legible.
    edges:
      - id: e-well-square
        to: square
        label: Step back to the square
        sort_order: 9
        effects: {log: "You step back."}
"""

STATIC["nodes-death.yaml"] = """\
# Death nodes end the run; they need no edges (cheat-death/rollback is the escape) and
# are exempt from the trap lint.
nodes:
  - id: peril
    arc: main
    type: death
    location: plaza
    title: "The Rusted Gate"
    is_death: true
    media: {image_theme: rusted-gate, music_theme: sudden-dread}
    body: >
      The rusted gate gives all at once — and the ground beneath it gives too. You fall
      into the dark, and the dark keeps you. (Cheat death to rewind.)
"""

STATIC["nodes-ending.yaml"] = """\
# Ending nodes resolve the spine. At least one is required.
nodes:
  - id: gate-out
    arc: main
    type: ending
    location: plaza
    title: "Out Through the Arch"
    media: {image_theme: village-arch-dawn, music_theme: release}
    body: >
      You pass under the arch and the village falls away behind you. Whatever this place
      was, you have left it. For now, the story rests here.
"""

STATIC["edges.yaml"] = """\
# Standalone edges (with an explicit `from`) attach a route to a node defined in ANY
# other file. This starter needs none — every edge lives inside its source node — but the
# file is here as the place to add cross-file routes, e.g.:
#
# edges:
#   - id: e-shortcut
#     from: square
#     to: gate-out
#     label: A shortcut
#     conditions: {all: []}
#     effects: {log: "..."}
#     sort_order: 5
edges: []
"""

STATIC["gates.yaml"] = """\
# AI dialogue gates. A gate node references one by id. The Actor plays the character; a
# separate Referee judges the criteria; only code applies on_success. `mercy_after_attempts`
# is a soft-lock safety net. See ../../../AI_DIALOGUE_GATES.md.
gates:
  - id: elder-greeting
    location: plaza
    character: elder
    intent: Win the old villager's trust so they let you pass through the arch.
    criteria:
      - {id: engaged, desc: "The player engages in good faith — is genuinely warm, curious, or honest, over more than a bare greeting. A lone 'hi'/'hello' or filler does NOT count."}
    success_rule: "engaged"
    knowledge_boundary:
      knows:
        - "the arch out of the village will let a newcomer pass once they mean no harm"
      refuses:
        - "anything about who the newcomer really is or how they got here"
      tone: "wary, guarded old villager; short sentences; warms to genuine kindness"
    hint_ladder:
      - "The villager watches you, saying nothing."
      - "\\"And why should I help you?\\" they murmur."
      - "\\"...You seem harmless enough. The arch will let you pass. Go, before I change my mind.\\""
    mercy_after_attempts: 3
    on_success:
      progress_points: 30
      set_flag: met_elder
      log: "The old villager decided you mean no harm — the arch will let you pass."
      announce: "The villager gives a slow nod. \\"The arch will let you pass now. Go.\\""
"""

STATIC["puzzles.yaml"] = """\
# Puzzles. A puzzle node references one by id. `solution.kind` is `exact` (case-insensitive
# single answer) or `set` (any of several accepted). The last hint is ~a giveaway.
puzzles:
  - id: well-riddle
    type: riddle
    prompt: "Carved round the well: \\"I have a mouth but never eat, a bed but never sleep, and I run without feet. What am I?\\""
    solution: {kind: set, set: [river, a river, the river]}
    required_clues: [well-clue]
    hint_ladder:
      - "It moves, always, yet stays in its place."
      - "It has banks, but keeps no money."
      - "It is a river. Answer: river."
    on_solve: {progress_points: 30, set_flag: solved_well, log: "The riddle answers itself — a river — and something shifts."}
"""

STATIC["clues.yaml"] = """\
# Clues are woven fragments a puzzle lists in `required_clues`; they surface when their
# `discover_conditions` hold (same DSL as edges).
clues:
  - id: well-clue
    puzzle: well-riddle
    placement: {location: plaza}
    reveal_text: "Someone has scratched a wavy line — like water — beneath the well's carved words."
    discover_conditions: {all: [{node_visited: square}]}
"""

# game.yaml + cells.yaml carry the CLI-substituted values (tokens replaced below).
GAME_YAML = """\
# Game-level metadata + engine configuration for this dataset. Optional; one such
# `game:` block per dataset (merged across files). The engine is data-agnostic — any key
# omitted here falls back to a built-in default (see api/app/gameconfig.py), so a minimal
# dataset still runs. title/subtitle/first_summary are seeded into the `games` registry
# table; the rest is read at runtime by gameconfig.
game:
  title: __TITLE__
  subtitle: __SUBTITLE__
  first_summary: __FIRST_SUMMARY__

  # Place/period the music director and image generator lean on. `region` is the fallback
  # when a world cell has no `region` of its own; `period` is appended to the atmosphere
  # setting string. Leave blank to stay place/time-agnostic.
  setting:
    region: __REGION__
    period: __PERIOD__

  # Title/particle tokens that are NOT part of a character's name (so "the Elder" is known
  # as "Elder"). Add your world's honorifics here.
  name_particles: [the]

  # Forced manual + comprehension quiz shown once before play (account-level). These three
  # questions cover the engine's core mechanics (the log, cheating death, talking to NPCs),
  # so they fit any game — reword or replace freely.
  onboarding:
    pass_threshold: 3
    manual: |
      HOW TO PLAY

      You wake somewhere unfamiliar. Act by choosing what to do and by talking to the
      people and places you meet. The game keeps a LOG of everything that happens — your
      log IS your progress.

      Some characters must be TALKED THROUGH: say the right kind of thing and they open
      up. Puzzles are hidden in the world — pay attention to what people say and what you
      read.

      You can die or get stuck. When that happens you can CHEAT DEATH: rewind your LOG to
      an earlier point and carry on — but it erases the progress you made after that point
      and costs you leaderboard position.

      Read this, then prove you've got the basics.
    quiz:
      - id: q-log
        prompt: "What represents your progress?"
        options:
          - "Your inventory of items"
          - "The log of everything that happens to you"
          - "Your character's health bar"
          - "The number of rooms you've unlocked"
        answer: 1
      - id: q-rollback
        prompt: "What happens when you cheat death to escape a bad situation?"
        options:
          - "Nothing — it's a free undo"
          - "You lose progress made after that point and drop on the leaderboard"
          - "You gain bonus Progress for surviving"
          - "Your account is reset"
        answer: 1
      - id: q-advance
        prompt: "How do you mainly get past the people you meet?"
        options:
          - "By fighting them"
          - "By paying them coins"
          - "By talking to them and saying the right kind of thing"
          - "By ignoring them and walking past"
        answer: 2

  # Deterministic offline soundtrack used when no LLM/Spotify is configured. Curate to your
  # game's mood.
  offline:
    tracks:
      - {artist: "Brian Eno", title: "An Ending (Ascent)", why: "ambient stillness"}
"""

CELLS_YAML = """\
# World cells: the travel grid. Each cell holds locations and has an `arrival_node` (where
# you land when you travel here). This starter has one cell.
cells:
  - id: home
    grid_x: 0
    grid_y: 0
    name: The Village
    kind: town
    region: __REGION__
    arrival_node: start
"""

CLAUDE_MD = """\
# CLAUDE.md — __TITLE_PLAIN__ game data

This folder is a **OneLife game dataset**: authored content as YAML, no code. The engine
mounts it and seeds it into Postgres; it appears in the in-app lobby as its own game.

Authoring rules + the full field reference live in the engine docs at the repo root:
`../../../AUTHORING.md`, `../../../STORY_AND_PUZZLES.md`, `../../../AI_DIALOGUE_GATES.md`.
Per-game engine config (setting, name particles, onboarding, offline soundtrack) lives in
the `game:` block of `game.yaml` — see `api/app/gameconfig.py` for the keys + defaults.

## Layout (all *.yaml are merged; organized by type)
- `arcs.yaml` `cells.yaml` `locations.yaml` `characters.yaml`
- `nodes-*.yaml` — story beats by node type, edges embedded inline
- `edges.yaml` — standalone edges (explicit `from`) into nodes in other files
- `gates.yaml` `puzzles.yaml` `clues.yaml`
- `game.yaml` — title/subtitle/first_summary + per-game config

## After editing
Always `make lint` (or `docker compose run --rm --no-deps api python -m app.seed --lint`),
then `make seed` to load. The lint proves the graph is completable (one entry, ≥1 ending,
no traps) and every reference resolves.
"""


def build_files(title: str, subtitle: str, first_summary: str,
                region: str, period: str) -> dict[str, str]:
    files = dict(STATIC)
    files["game.yaml"] = (GAME_YAML
                          .replace("__TITLE__", yq(title))
                          .replace("__SUBTITLE__", yq(subtitle))
                          .replace("__FIRST_SUMMARY__", yq(first_summary))
                          .replace("__REGION__", yq(region))
                          .replace("__PERIOD__", yq(period)))
    files["cells.yaml"] = CELLS_YAML.replace("__REGION__", yq(region))
    files["CLAUDE.md"] = CLAUDE_MD.replace("__TITLE_PLAIN__", title)
    return files


def prettify(game_id: str) -> str:
    return re.sub(r"[-_]+", " ", game_id).strip().title()


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold a new OneLife game.")
    ap.add_argument("--id", required=True, help="Game id / folder name (slug: A-Z a-z 0-9 _ -).")
    ap.add_argument("--title", default=None, help="Lobby title (default: prettified id).")
    ap.add_argument("--subtitle", default="", help="Lobby subtitle.")
    ap.add_argument("--first-summary", dest="first_summary", default="You wake.",
                    help="First log line at the entry node.")
    ap.add_argument("--region", default="", help="game.setting.region (blank = agnostic).")
    ap.add_argument("--period", default="", help="game.setting.period (blank = agnostic).")
    ap.add_argument("--force", action="store_true", help="Overwrite an existing game folder.")
    ap.add_argument("--dry-run", action="store_true", help="Print the plan; write nothing.")
    args = ap.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.id):
        print(f"ERROR: --id '{args.id}' must match [A-Za-z0-9_-]+ (no spaces/slashes).",
              file=sys.stderr)
        return 2

    title = args.title or prettify(args.id)
    data_dir = REPO_ROOT / "games" / args.id / "data"
    files = build_files(title, args.subtitle, args.first_summary, args.region, args.period)

    rel = data_dir.relative_to(REPO_ROOT)
    if args.dry_run:
        print(f"[dry-run] would create {rel}/ with {len(files)} files:")
        for name in sorted(files):
            print(f"  - {rel}/{name}")
        print("\nStory graph: start(entry) -> square -> {elder(gate), well(puzzle), "
              "gate-out(ending), peril(death)}; every non-death node reaches the ending.")
        print(f"Title: {title!r}  region: {args.region!r}  period: {args.period!r}")
        return 0

    if data_dir.exists() and not args.force:
        print(f"ERROR: {rel} already exists. Pass --force to overwrite.", file=sys.stderr)
        return 1

    data_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (data_dir / name).write_text(content, encoding="utf-8")

    print(f"Created game '{args.id}' at {rel}/ ({len(files)} files).")
    print("\nNext steps (from the repo root):")
    print("  1. Lint:  docker compose run --rm --no-deps api python -m app.seed --lint")
    print("  2. Seed:  docker compose run --rm api python -m app.seed")
    print(f"  3. It now appears in the lobby (GET /api/games) as '{title}'.")
    print("\nTheme it by editing the YAML in that folder (see its CLAUDE.md). Optional map "
          "art: /generateMap and /generateIcons.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
