# CLAUDE.md — "The Long Way to Lund" (game id `SSandby1992`)

A OneLife game dataset: a 1992 coming-of-age mystery. You are 15; your brother Mattias
went silent six months ago ("he moved to Lund"); you retrace his route from Södra Sandby
to Lund and uncover the truth — he didn't abandon you, and your father hid the letters.

This is **content, not code**. The engine lives at the repo root and is data-agnostic; see
the root `CLAUDE.md` and `AUTHORING.md` for engine mechanics, and `data/CLAUDE.md` for the
per-file authoring rules. This file is the map of *this game* and its design invariants.

## Layout
- `data/` — the dataset (all `*.yaml`, organized by type). This is what the engine seeds.
- `data/CLAUDE.md` — authoring rules + the lint/seed loop for the dataset.
- `i18n/sv.yaml` — Swedish translation sidecar (≈306 leaves); loaded per-game at seed time.
- `game-description.txt` — the design brief (premise, hidden truth, spine, node flow). The
  **source of intent**; read it before reworking the story. The "rift" (why Mattias left)
  is the one swappable dial — the estranged-brother → intercepted-messages → reunion
  machinery stays identical if you reskin it.

## The spine (see `game-description.txt` for the full flow)
`start` (entry narration, at home) → `home` "Mattias's Room" (find tape + photo ⇒ `goal_set`)
→ `centrum` → `trailpath` → `ostra-torn` → `lundcentral` (hub) → record shop / cathedral /
corridor → `flat` (reunion). Three cells laid west-to-east: **sandby → trail → lund**.

- **Gates** (`nodes-gate.yaml`): `mum` `dad` `krister` (Sandby) · `recordshop` (Sonny)
  `corridor` (Robin). Band name ⇒ Sonny; the truth from Robin ⇒ the flat.
- **Puzzles** (`puzzles.yaml`): `mixtape-cipher` (tracklist initials = band) · `phone-number`
  (missing digits = a date from the log) · `cathedral-clock` (noon) · `letters-box`
  (combination = Mattias's birthday ⇒ the true ending).
- **Endings** (`nodes-ending.yaml`): `ending-true` (needs the letters) · `ending-partial`
  (found him, never learned about the letters) · `ending-home` (turn back).
- **Deaths** (`nodes-death.yaml`): `death-road` (väg 11) · `death-marlpit`.

## Design invariants specific to this game — don't break these
- **Guided road, no open travel.** No node sets `world_access`; movement is entirely by
  authored story edges (`cells.yaml` says so explicitly). Consequences to remember:
  - The player-facing **overview map** reveals a cell only when you *walk into* it (engine
    discovers the destination's own cell on every edge; there is no travel-map here).
  - Endings/NPCs share their **place** with a real location so the map draws one icon per
    place — e.g. the "Home Again" ending is anchored at `location: home`, not a phantom of
    its own, so it resolves to the same spot as Mattias's room. Keep new terminal/NPC nodes
    anchored to a real location for the same reason.
- **Exactly one entry** (`start`, `entry: true`), ≥1 ending, no traps — enforced by lint.
- **Puzzles are solved from the log**, not from items (pure 1992: names, dates, digits).
- Keep the **offline soundtrack** and `setting` (southern Sweden, summer 1992) era-true;
  they drive the music director / image generator when no LLM is configured.

## Working on it
- Edit `data/*.yaml`, then from the repo root: `make lint` (proves the spine is completable),
  then `make seed` (mounted volume — no rebuild). Translations in `i18n/sv.yaml` reseed too.
- **Engine changes** (`api/app/*.py`) are baked into the image → rebuild the api container;
  on this machine use the prod overlay (`-f docker-compose.yml -f docker-compose.prod.yml`).
- Map images: node icons under `data/images/maps/icons/`, parchment backdrop
  `data/images/maps/parchment2.png`. This game does **not** use a pre-rendered `world.png`
  (that only backs the open-travel map, which this game never shows).
