# OneLife — KBK Mystery (game data)

This repo is the **game data** for the [OneLife](../OneLife) engine: a dark
text-adventure set in southern Sweden (Skåne), opening at Killebäckskolan in
Södra Sandby. It contains no code — only authored content as YAML.

The OneLife engine is data-agnostic; it mounts this repo at `/content` and seeds
it into Postgres. By default the engine expects this repo as a sibling directory
(`../OneLife-KBK-mystery`); override with `GAME_DATA_DIR` in the engine's `.env`.

## Files

Each `*.yaml` may contain any subset of the top-level lists `arcs`, `cells`,
`characters`, `locations`, `nodes`, `gates`, `puzzles`, `clues`, `edges`. They are
all merged, so organize by area/feature:

| File | What it holds |
|---|---|
| `world.yaml` | the cell grid + travel hubs (Sandby / Lund) |
| `killebackskolan.yaml` | the opening: the school, the janitor, the boiler-room puzzle |
| `marta.yaml` | Märta (cross-character memory leakage) + the chalk-letters puzzle |
| `lund.yaml` | Lund: cathedral library, cellar, lodgings, manor, botanical garden, crypt… |
| `malmo.yaml` | Malmö: harbour, Rosengård, Stortorget, Malmöhus, Turning Torso… |
| `sandby.yaml` | Södra Sandby sub-locations: church, gravel-pit motel, the crooked house |
| `npcs.yaml` | the 10-NPC roster (personas the AI Actor reads) |
| `puzzles.yaml` | a library of standalone puzzles (riddles, combinations, word-locks) |

## Working with it

From the **engine** repo (`../OneLife`):

```bash
make lint   # validate this data — schema, references, spine reachability (no DB)
make seed   # validate + load it into Postgres
```

Authoring rules and the full field reference live in the engine's
[`AUTHORING.md`](../OneLife/AUTHORING.md). The validator enforces references and a
spine-reachability lint (exactly one entry node, ≥1 ending, no traps).
