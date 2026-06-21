---
name: playtest
description: Auto-traverse the OneLife game to find broken or odd spots — soft-locks/dead-ends, unreachable content, dangling references, and authoring oddities — then report findings with suggested fixes. Use when asked to playtest, traverse, or "play through" the game looking for bugs, oddities, or stuck points. Local to the OneLife repo.
---

# Playtest the OneLife game

Two layers: a fast **static traversal** that models how the engine actually
renders choices (so it catches traps the seed-time lint can't), and an optional
**live playthrough** for narrative oddities a static pass can't see.

Always start with the static pass. Add the live pass when asked to "actually
play it", or to confirm a narrative/AI issue (NPC tone, phantom instructions).

## 1. Static traversal (always run this first)

From the repo root:

```bash
python .claude/skills/playtest/analyze.py
```

- Needs only `python` + `pyyaml`. No DB, no running stack.
- It finds the game-data automatically: `$GAME_DATA_DIR`, else `$CONTENT_DIR`,
  else the sibling `../OneLife-KBK-mystery`. Override by passing a path:
  `python .claude/skills/playtest/analyze.py D:/source/OneLife-KBK-mystery`.
- Exit code is **1** if there are any ERROR findings, else 0.

### What it checks
- **Soft-locks / dead-ends** — walks the reachable `(node, progress)` state space
  using the engine's real edge-visibility (conditions + the "hide finished
  gates/puzzles" rule + its anti-soft-lock fallback) plus pass-gate / solve-puzzle
  / world-map-travel transitions. Flags any non-terminal, non-world_access node
  that can be reached with **no way out** even after finishing its own gate/puzzle.
  (This is the class of bug the lint misses — e.g. a sub-puzzle whose only edges
  return to a now-passed gate node.)
- **Unreachable nodes** — dead content never reachable from the entry (travel-aware).
- **Dangling references** — edge/`body_variants` conditions pointing at unknown
  gates/puzzles; gate `story_node_next` to unknown nodes; edges to/from unknown nodes.
- **Flag typos** — a condition that needs a flag no effect ever sets (so it can
  never unlock / the variant can never show).
- **Authoring oddities** — empty puzzle prompt (player sees no clue), puzzle with
  no hint_ladder, malformed solution, gate with no `mercy_after_attempts`,
  `body_variants` entries with no `body`.

### Reading the output
Findings are grouped by severity:
- **ERROR** — a real defect (soft-lock, broken reference). Fix before shipping.
- **WARN** — likely a problem or dead content; judge in context (e.g. an
  intentionally-unreachable draft node).
- **INFO** — notes.

Report the ERRORs and meaningful WARNs to the user with the suggested fix from
each finding. **Don't edit content unless asked** — propose, then apply on request.
After applying any content fix, re-run the harness and also `make lint`
(or `docker compose run --rm --no-deps api python -m app.seed --lint`).

### Keep it faithful
`analyze.py` re-implements two pieces of engine logic; if either changes, update
the harness to match:
- the condition DSL → `api/app/dsl.py` (`evaluate`)
- edge visibility → `api/app/engine.py` (`render_state`, the `passing`/`shown` block)

## 2. Live playthrough (optional, deeper)

Catches things the static pass can't: an NPC (the AI Actor) telling the player to
do something the game has no node for, stale gate text, broken transitions, wrong
puzzle responses. Requires the stack running.

1. Start it: `docker compose up -d` (from the repo root). Wait for
   `GET http://localhost:8000/api/health` to return `{"ok": true}`.
2. Make a player (auth has TOTP + a forced quiz):
   - `POST /api/auth/register {email,password,display_name}` → returns `secret`.
   - compute a 6-digit TOTP from `secret` (standard RFC-6238, 30s/SHA1).
   - `POST /api/auth/totp/enable {email,password,code}`
   - `POST /api/auth/login {email,password,code}` → `token` (send as `Authorization: Bearer`).
   - `POST /api/onboarding/submit {answers:{"q-log":1,"q-rollback":1,"q-advance":2}}`
3. Walk the world:
   - `GET /api/state` → current `node` (with `body`, `puzzle.prompt`), `edges`.
   - `POST /api/edge {edge_id}` to move.
   - `POST /api/gate/message {text}` to talk through a gate (passes on criteria
     met, or by mercy after ~6 turns).
   - `POST /api/puzzle/submit {answer}` for puzzles.
   Prioritise the nodes the static pass flagged, plus visit each NPC gate once.
4. Watch for: a node returning **zero edges** (soft-lock), an NPC giving an
   instruction with no matching choice (**phantom task**), body text that
   contradicts the world state, a puzzle whose response never shows, or any HTTP
   error. Report each with the node id and a suggested fix.

## Reporting
Summarise: what was traversed, ERROR/WARN counts, each finding with location and a
concrete suggested fix. If asked to fix, apply, then re-run the static pass (and
`make lint`) to confirm green.
