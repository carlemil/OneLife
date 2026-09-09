# CLAUDE.md — "Flykten från fritids" (game id `FlyktenFranFritids`)

A OneLife game dataset: a Swedish text adventure for nine-year-olds. Arthur (the player)
and Oliver (his best friend, always at his side) sneak out of Uggleskolan's fritids at
16:20 to fetch the treasure map from the locked classroom, cross Södra Sandby to a hut in
Fågelsångsdalen, dig up the tin — and must be back before mum arrives at 17:15.
Mischievous and kind. Grown-ups are nice but in the way. **There is no game over.**

This is **content, not code**. The engine lives at the repo root (see the root `CLAUDE.md`
and `AUTHORING.md`); `data/CLAUDE.md` has the per-file authoring rules. This file is the
map of *this game* and its design invariants.

## Layout
- `data/` — the dataset (all `*.yaml`, by type). **Authored directly in Swedish** — there is
  no `i18n/` sidecar; `game.language: sv` switches the UI chrome to Swedish on selection.
- `game-description.html` — the original storyboard (from `D:\source\sandby-aventyr`). The
  **source of intent**: scenes, timings, puzzles, endings and the open questions that were
  settled here (see below). Read it before reworking the story.

## The clock is the game
- `game.clock.start: "16:20"`; `story_time` counts **minutes** from there and the engine shows
  it as HH:MM in the header. Every edge carries `advance_story_time` (1–3 min, from the
  storyboard). Wrong puzzle answers cost minutes via `on_fail`.
- Thresholds: **16:30 = 10** (Bosse locks the back gate → climb the compost, or have helped
  him), **17:15 = 55** (pickup; later = "Upptäckta").
- Fastest path is home ≈ 17:03 — about twelve minutes of slack for detours (ängen +5,
  komposten +2, the dark log +3, a wrong stone +2, Lena's scolding +5).

## The spine
`start` → `fritidsrummet` (hub; Sigrid gate, Emilia via `forskolan`) → `korridoren` →
`affischen` (riddle, 7) → `dan` (gate: give him a riddle → `dan_at_board` → take keys) →
`klassrum3` (puzzle: blue key → `have_backpacks` = lamp + map + 20 kr) → `skolgarden`
(help Bosse) → `cykelstallen` → `cykelvagen` → (cycle path | `villakvarteren` + Margit gate)
→ `centrum` (help Ali → bun + directions | buy a bun) → `stigen` (bun to Ludde | ängen) →
`backen` (lamp or vest) → `kojan` (puzzle: third/flat stone) → `skatten` → `tillbaka` → ending.

- **Gates** (`gates.yaml`): `dan-gatan` · `sigrid-tuggummi` · `margit-halsa`. Hint ladders are
  Oliver whispering. Mercy after 4 attempts — a child must never get stuck.
- **Puzzles** (`puzzles.yaml`): `veckans-klurighet` (7) · `ratt-nyckel` (blå) · `ratt-sten`
  (tredje / den platta). All have `on_fail: {advance_story_time, log}`.
- **Endings** (`nodes-ending.yaml`), chosen by `tillbaka`'s edge conditions:
  `ending-kompis` if `emilia_along`; else `ending-upptackta` if `story_time_gte: 55` OR
  `not sigrid_covers` OR `margit_called`; else `ending-ingen-markte`.
- **No death nodes.** `nodes-death.yaml` is intentionally empty.

## Flag glossary
`have_gum` (start; spent on Sigrid) · `have_riddle` · `dan_at_board` · `have_keys` ·
`have_backpacks` (lamp + map + 20 kr, one flag) · `keys_returned` (bonus line from Dan) ·
`sigrid_covers` · `lied_to_sigrid` · `emilia_along` + `have_vest` · `emilia_promised` ·
`bosse_helped` · `left_school` · `margit_ok` / `margit_called` · `ali_helped` · `have_bun` /
`spent_20kr` · `ludde_fed` / `ludde_petted` · `oliver_wet` · `have_treasure`.

## Design invariants — don't break these
- **Items are flags**, Oliver is prose (node bodies + hint ladders), time is `story_time`.
  Nothing random: Margit *always* calls unless greeted (storyboard's open question, settled).
- **No map** (`game.map: false`): no `map:` coordinates, no icons, no `world_access`. Every
  move is an authored edge phrased as a **command** ("Gå norr…", "Ta…", "Ge…", "Hjälp…").
- **Gates never move the player** — each success sets a flag; a conditional edge does the rest.
  The `korridoren → dan` edge uses `keep_when_finished` so the keys can be taken/returned.
- **Kind tone, never scary.** Mistakes cost minutes, not lives.
- The tin holds a **hook for the next adventure** (a brass key tagged KVARNEN and a note from
  "R."). Keep it a mystery.

## Working on it
- Edit `data/*.yaml`, then from the repo root: `make lint`, then `make seed` (mounted volume —
  no rebuild). Re-editing the `game:` block (clock, language, map) needs an api restart.
- Static check: `python .claude/skills/playtest/analyze.py games/FlyktenFranFritids/data`.
- **Engine changes** need an api rebuild — on this machine always with the prod overlay
  (`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build api web`).
