#!/usr/bin/env python3
"""OneLife playtest harness - static, runtime-faithful traversal of the story
graph to surface soft-locks, dead-ends, unreachable content, dangling
references and authoring oddities.

It loads the YAML game-data repo (the same files the engine seeds), then walks
the graph the way app.engine.render_state actually shows choices to a player -
including the "hide finished gates/puzzles" rule and its anti-soft-lock
fallback - exploring the reachable (node, progress) state space. Because it
models real edge visibility, it catches traps the seed-time lint cannot (the
lint is optimistic and ignores conditions).

Usage:
  python analyze.py [CONTENT_DIR]
CONTENT_DIR defaults to $GAME_DATA_DIR, then $CONTENT_DIR, then the sibling
../OneLife-KBK-mystery repo. Exit code 1 if any ERROR-level finding is reported.
"""
import os
import sys
import glob
import collections

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

LIST_KEYS = ["arcs", "cells", "characters", "locations", "nodes", "gates",
             "puzzles", "clues", "edges"]
MAX_STATES = 400_000   # safety cap on the (node, progress) BFS


# --------------------------------------------------------------------------- #
#  Condition DSL - mirrors app.dsl.evaluate exactly. Keep in sync.
# --------------------------------------------------------------------------- #
def evaluate(cond, ctx) -> bool:
    if not cond:
        return True
    if "all" in cond:
        return all(evaluate(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, ctx) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], ctx)
    if "flag_set" in cond:
        return cond["flag_set"] in ctx["flags"]
    if "node_visited" in cond:
        return cond["node_visited"] in ctx["visited"]
    if "gate_passed" in cond:
        return cond["gate_passed"] in ctx["gates"]
    if "puzzle_solved" in cond:
        return cond["puzzle_solved"] in ctx["solved"]
    if "name_known" in cond:
        return cond["name_known"] in ctx.get("known_names", set())
    if "clue_found" in cond:
        return cond["clue_found"] in ctx["clues"]
    if "story_time_gte" in cond:
        return ctx["story_time"] >= int(cond["story_time_gte"])
    return False   # unknown predicate -> fail closed, like the engine


def reach_evaluate(cond, ctx) -> bool:
    """Optimistic variant of `evaluate`, used ONLY by the monotonic reachability
    walk. That walk grows one global `progress` set that never shrinks, so a
    negated precondition like `{not: {flag_set: letters_read}}` would be pinned
    false forever the instant the flag became obtainable anywhere - falsely
    marking any node behind it (e.g. a "you didn't do the optional thing" ending)
    as unreachable. Reachability only asks "could SOME play reach this node", so
    here a `not` is treated as satisfiable: we assume the player can be in a state
    where the inner condition is still false. Trade-off: like the seed-time lint's
    optimism, this can under-report a genuinely dead node hidden behind a negation
    that is in fact forced true on every path - the precise (node, progress)
    soft-lock BFS below and a live playthrough remain the rigorous checks."""
    if not cond:
        return True
    if "all" in cond:
        return all(reach_evaluate(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(reach_evaluate(c, ctx) for c in cond["any"])
    if "not" in cond:
        return True   # optimistic: assume the negated precondition can be unmet
    return evaluate(cond, ctx)   # positive predicates: optimistic vs. the max progress set


# --------------------------------------------------------------------------- #
#  Load + normalize content (mirrors app.content.load_dir / all_edges)
# --------------------------------------------------------------------------- #
def load_dir(path):
    data = {k: [] for k in LIST_KEYS}
    files = sorted(glob.glob(os.path.join(path, "*.yaml")) +
                   glob.glob(os.path.join(path, "*.yml")))
    for f in files:
        with open(f, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        for k in LIST_KEYS:
            data[k].extend(doc.get(k, []) or [])
    return data, files


def all_edges(data):
    edges = []
    for n in data["nodes"]:
        for i, e in enumerate(n.get("edges", []) or []):
            e = dict(e)
            e.setdefault("from", n["id"])
            e.setdefault("sort_order", i)
            edges.append(e)
    for e in data["edges"]:
        e = dict(e)
        e.setdefault("sort_order", 0)
        edges.append(e)
    return edges


def resolve_content_dir(argv):
    if len(argv) > 1:
        return argv[1]
    for env in ("GAME_DATA_DIR", "CONTENT_DIR"):
        if os.environ.get(env):
            return os.environ[env]
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    return os.path.abspath(os.path.join(repo_root, "..", "OneLife-KBK-mystery"))


# --------------------------------------------------------------------------- #
#  Findings
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self):
        self.items = []   # (severity, category, where, message, suggestion)

    def add(self, sev, cat, where, msg, fix=""):
        self.items.append((sev, cat, where, msg, fix))

    def error(self, *a): self.add("ERROR", *a)
    def warn(self, *a): self.add("WARN", *a)
    def info(self, *a): self.add("INFO", *a)

    def dump(self):
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        self.items.sort(key=lambda x: (order[x[0]], x[1], x[2]))
        counts = collections.Counter(i[0] for i in self.items)
        print("=" * 72)
        print("OneLife playtest findings")
        print("=" * 72)
        for sev, cat, where, msg, fix in self.items:
            print(f"\n[{sev}] {cat} - {where}")
            print(f"  {msg}")
            if fix:
                print(f"  fix: {fix}")
        print("\n" + "-" * 72)
        print(f"ERROR={counts['ERROR']}  WARN={counts['WARN']}  INFO={counts['INFO']}")
        return counts["ERROR"]


# --------------------------------------------------------------------------- #
#  Reference collection (for dangling-id checks)
# --------------------------------------------------------------------------- #
def collect_refs(cond, out):
    if not isinstance(cond, dict):
        return
    for key in ("all", "any"):
        for c in cond.get(key, []) or []:
            collect_refs(c, out)
    if "not" in cond:
        collect_refs(cond["not"], out)
    for pred, bucket in (("flag_set", "flags"), ("gate_passed", "gates"),
                         ("puzzle_solved", "puzzles"), ("node_visited", "nodes"),
                         ("clue_found", "clues"), ("name_known", "characters")):
        if pred in cond:
            out[bucket].add(cond[pred])


def main():
    cdir = resolve_content_dir(sys.argv)
    if not os.path.isdir(cdir):
        sys.exit(f"content dir not found: {cdir}")
    data, files = load_dir(cdir)
    rep = Report()

    nodes = {n["id"]: n for n in data["nodes"]}
    gates = {g["id"]: g for g in data["gates"]}
    puzzles = {p["id"]: p for p in data["puzzles"]}
    clue_ids = {c["id"] for c in data["clues"]}
    char_ids = {c["id"] for c in data.get("characters", []) or []}
    edges = all_edges(data)

    out_edges = collections.defaultdict(list)
    for e in edges:
        out_edges[e.get("from")].append(e)
    for fn in out_edges:
        out_edges[fn].sort(key=lambda e: e.get("sort_order", 0))

    # World-map travel (mirrors the engine's travel-aware lint): from any
    # world_access node a player can open the map and travel to any cell's
    # arrival node, so those are reachable and never a dead-end.
    arrivals = [c.get("arrival_node") for c in data["cells"]
                if c.get("arrival_node") in nodes]

    entries = [n["id"] for n in data["nodes"] if n.get("entry")]
    endings = {n["id"] for n in data["nodes"] if n.get("type") == "ending"}
    deaths = {n["id"] for n in data["nodes"]
              if n.get("type") == "death" or n.get("is_death")}
    terminal = endings | deaths

    # ---- flags ever set anywhere (for typo / unreachable-flag detection) ----
    # `set_flag` is one flag or a list of flags (engine.apply_action accepts both).
    def set_flags(effects):
        f = (effects or {}).get("set_flag")
        return [] if not f else (list(f) if isinstance(f, list) else [f])

    declared_flags = set()
    for e in edges:
        declared_flags.update(set_flags(e.get("effects")))
    for g in gates.values():
        declared_flags.update(set_flags(g.get("on_success")))
    for p in puzzles.values():
        declared_flags.update(set_flags(p.get("on_solve")))

    # ---- helpers to read a node's progress contribution -------------------
    def gate_flags(node):
        g = gates.get(node.get("gate"))
        toks = {("gate", node.get("gate"))}
        for f in set_flags((g or {}).get("on_success")):
            toks.add(("flag", f))
        return toks

    def puzzle_flags(node):
        p = puzzles.get(node.get("puzzle"))
        toks = {("puzzle", node.get("puzzle"))}
        for f in set_flags((p or {}).get("on_solve")):
            toks.add(("flag", f))
        return toks

    def ctx_from(state, visited):
        return {
            "flags": {x for t, x in state if t == "flag"},
            "gates": {x for t, x in state if t == "gate"},
            "solved": {x for t, x in state if t == "puzzle"},
            "visited": visited,
            "clues": clue_ids,            # optimistic: clues discoverable
            "story_time": 10 ** 9,
        }

    # ---- engine-faithful visible-edge computation (mirrors render_state) ----
    def visible(node_id, state, visited):
        ctx = ctx_from(state, visited)
        passing = []
        for e in out_edges.get(node_id, []):
            if not evaluate(e.get("conditions"), ctx):
                continue
            t = nodes.get(e.get("to"))
            finished = t is not None and (
                (t.get("type") == "gate" and t.get("gate") in ctx["gates"])
                or (t.get("type") == "puzzle" and t.get("puzzle") in ctx["solved"]))
            passing.append((e, finished))
        shown = [e for e, fin in passing if not fin] or [e for e, _ in passing]
        return shown

    # ======================================================================
    #  Structural / reference lints
    # ======================================================================
    if len(entries) != 1:
        rep.error("structure", "entry", f"expected exactly one entry node, found {len(entries)}: {entries}",
                  "mark exactly one node with `entry: true`")
    if not endings:
        rep.error("structure", "endings", "no ending node defined",
                  "give at least one node `type: ending`")

    for e in edges:
        if e.get("from") not in nodes:
            rep.error("reference", f"edge {e.get('id','?')}", f"`from` unknown node {e.get('from')}", "fix the id")
        if e.get("to") not in nodes:
            rep.error("reference", f"edge {e.get('id','?')}", f"`to` unknown node {e.get('to')}", "fix the id")
        refs = {"flags": set(), "gates": set(), "puzzles": set(), "nodes": set(), "clues": set(), "characters": set()}
        collect_refs(e.get("conditions"), refs)
        for fl in refs["flags"]:
            if fl not in declared_flags:
                rep.warn("reference", f"edge {e.get('id','?')}",
                         f"condition needs flag '{fl}' that no effect ever sets",
                         "typo, or this edge can never unlock - set the flag somewhere or fix the name")
        for gp in refs["gates"]:
            if gp not in gates:
                rep.error("reference", f"edge {e.get('id','?')}", f"gate_passed references unknown gate '{gp}'", "fix the id")
        for pz in refs["puzzles"]:
            if pz not in puzzles:
                rep.error("reference", f"edge {e.get('id','?')}", f"puzzle_solved references unknown puzzle '{pz}'", "fix the id")
        for ch in refs["characters"]:
            if ch not in char_ids:
                rep.error("reference", f"edge {e.get('id','?')}", f"name_known references unknown character '{ch}'", "fix the id")

    for n in data["nodes"]:
        nid = n["id"]
        if n.get("type") == "gate":
            gid = n.get("gate")
            if gid not in gates:
                rep.error("reference", f"node {nid}", f"gate node references unknown gate '{gid}'", "fix the id")
            elif "mercy_after_attempts" not in gates[gid]:
                rep.warn("gate", f"node {nid}", f"gate '{gid}' has no mercy_after_attempts (soft-lock risk)",
                         "add mercy_after_attempts so the conversation can never dead-end")
            sn = (gates.get(gid) or {}).get("on_success", {}).get("story_node_next")
            if sn and sn not in nodes:
                rep.error("reference", f"gate {gid}", f"on_success.story_node_next unknown node '{sn}'", "fix the id")
        if n.get("type") == "puzzle":
            pid = n.get("puzzle")
            if pid not in puzzles:
                rep.error("reference", f"node {nid}", f"puzzle node references unknown puzzle '{pid}'", "fix the id")
            else:
                pz = puzzles[pid]
                if not (pz.get("prompt") or "").strip():
                    rep.warn("puzzle", f"node {nid}", f"puzzle '{pid}' has an empty prompt - player sees no question",
                             "give the puzzle a `prompt` (it is shown as the on-screen clue)")
                if not pz.get("hint_ladder"):
                    rep.warn("puzzle", f"node {nid}", f"puzzle '{pid}' has no hint_ladder", "add an escalating hint_ladder")
                sol = pz.get("solution") or {}
                if sol.get("kind") == "exact" and "value" not in sol:
                    rep.error("puzzle", f"puzzle {pid}", "exact solution has no `value`", "add solution.value")
                if sol.get("kind") == "set" and not sol.get("set"):
                    rep.error("puzzle", f"puzzle {pid}", "set solution has an empty `set`", "add accepted answers to solution.set")
        # body_variants references
        for i, v in enumerate(n.get("body_variants", []) or []):
            if "body" not in v:
                rep.warn("body_variants", f"node {nid}", f"variant #{i} has no `body`", "add a body string")
            refs = {"flags": set(), "gates": set(), "puzzles": set(), "nodes": set(), "clues": set(), "characters": set()}
            collect_refs(v.get("when"), refs)
            for gp in refs["gates"]:
                if gp not in gates:
                    rep.error("body_variants", f"node {nid}", f"variant #{i} when.gate_passed unknown gate '{gp}'", "fix the id")
            for pz in refs["puzzles"]:
                if pz not in puzzles:
                    rep.error("body_variants", f"node {nid}", f"variant #{i} when.puzzle_solved unknown puzzle '{pz}'", "fix the id")
            for ch in refs["characters"]:
                if ch not in char_ids:
                    rep.error("body_variants", f"node {nid}", f"variant #{i} when.name_known unknown character '{ch}'", "fix the id")
            for fl in refs["flags"]:
                if fl not in declared_flags:
                    rep.warn("body_variants", f"node {nid}", f"variant #{i} when.flag_set '{fl}' is never set by any effect",
                             "typo, or this variant can never show")

    # ======================================================================
    #  Optimistic reachability (for node_visited satisfaction + dead content)
    # ======================================================================
    reached, progress = set(), set()
    if entries:
        reached.add(entries[0])
        changed = True
        while changed:
            changed = False
            for nid in list(reached):
                node = nodes[nid]
                if node.get("type") == "gate":
                    for tok in gate_flags(node):
                        if tok not in progress:
                            progress.add(tok); changed = True
                if node.get("type") == "puzzle":
                    for tok in puzzle_flags(node):
                        if tok not in progress:
                            progress.add(tok); changed = True
                ctx = ctx_from(progress, reached)
                for e in out_edges.get(nid, []):
                    if reach_evaluate(e.get("conditions"), ctx):
                        for f in set_flags(e.get("effects")):
                            if ("flag", f) not in progress:
                                progress.add(("flag", f)); changed = True
                        to = e.get("to")
                        if to in nodes and to not in reached:
                            reached.add(to); changed = True
                if node.get("world_access"):
                    for an in arrivals:
                        if an not in reached:
                            reached.add(an); changed = True

    for nid in nodes:
        if nid not in reached:
            rep.warn("reachability", f"node {nid}", "unreachable from the entry (dead content)",
                     "wire an edge to it, or remove it")

    # ======================================================================
    #  Runtime dead-end / soft-lock detection - BFS over (node, progress)
    #  using the engine's real edge-visibility, with pass-gate / solve-puzzle
    #  transitions. A node is a dead-end if, even after passing its gate /
    #  solving its puzzle, it shows zero choices and is not terminal.
    # ======================================================================
    if entries:
        # Seed with the entry and every cell arrival hub (all reachable by map
        # travel), then explore each area's edges - far cheaper than branching
        # travel from every world_access node in every state.
        starts = [(entries[0], frozenset())] + [(an, frozenset()) for an in arrivals]
        seen = set(starts)
        dq = collections.deque(starts)
        deadends = {}      # node_id -> example progress tokens
        capped = False
        while dq:
            if len(seen) > MAX_STATES:
                capped = True
                break
            nid, state = dq.popleft()
            node = nodes[nid]
            vis = visible(nid, state, reached)

            # Local actions that could unlock an exit: pass gate / solve puzzle.
            unlock_state = set(state)
            if node.get("type") == "gate" and ("gate", node.get("gate")) not in state:
                unlock_state |= gate_flags(node)
            if node.get("type") == "puzzle" and ("puzzle", node.get("puzzle")) not in state:
                unlock_state |= puzzle_flags(node)
            vis_after = visible(nid, frozenset(unlock_state), reached)

            # world_access nodes always have the map as a way out.
            if nid not in terminal and not node.get("world_access") and not vis_after:
                deadends.setdefault(nid, state)

            # Expand: pass-gate transition
            if node.get("type") == "gate" and ("gate", node.get("gate")) not in state:
                ns = (nid, frozenset(state | gate_flags(node)))
                if ns not in seen:
                    seen.add(ns); dq.append(ns)
            # Expand: solve-puzzle transition
            if node.get("type") == "puzzle" and ("puzzle", node.get("puzzle")) not in state:
                ns = (nid, frozenset(state | puzzle_flags(node)))
                if ns not in seen:
                    seen.add(ns); dq.append(ns)
            # Expand: take each visible edge
            for e in vis:
                ns_state = state | {("flag", f) for f in set_flags(e.get("effects"))}
                ns = (e.get("to"), frozenset(ns_state))
                if e.get("to") in nodes and ns not in seen:
                    seen.add(ns); dq.append(ns)

        for nid, state in deadends.items():
            toks = ", ".join(sorted(f"{t}:{x}" for t, x in state)) or "(start state)"
            rep.error("soft-lock", f"node {nid}",
                      f"reachable with NO way out - even after any gate/puzzle here is finished, "
                      f"it shows zero choices. Example state: {toks}",
                      "add an unconditional exit edge, or point its return edge at a plain "
                      "location/hub instead of a gate node (whose edge is hidden once passed)")
        if capped:
            rep.warn("soft-lock", "(bfs)", f"state space exceeded {MAX_STATES} - dead-end scan is partial",
                     "raise MAX_STATES or narrow the content if this persists")

    # ---- summary line ----
    print(f"content dir : {cdir}")
    print(f"files       : {len(files)}")
    print(f"nodes       : {len(nodes)}  (entry={entries}, endings={len(endings)}, deaths={len(deaths)})")
    print(f"edges       : {len(edges)}")
    print(f"reachable   : {len(reached)}/{len(nodes)} nodes")
    errs = rep.dump()
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
