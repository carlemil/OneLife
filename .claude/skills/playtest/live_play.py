#!/usr/bin/env python3
"""Live playthrough driver for OneLife. Drives the real API to catch narrative
oddities a static pass can't: phantom NPC instructions, zero-edge soft-locks,
wrong puzzle responses, HTTP errors. Requires the stack up on :8000."""
import base64, glob, hashlib, hmac, json, os, struct, sys, time, urllib.request, urllib.error

API = "http://localhost:8000"
CONTENT = os.environ.get("GAME_DATA_DIR") or os.environ.get("CONTENT_DIR") or r"D:\source\OneLife-KBK-mystery"

# ----------------------------------------------------------------------------- http
def req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"detail": str(e)}

def totp(secret):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int(time.time()) // 30)
    h = hmac.new(key, counter, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"

# ----------------------------------------------------------------------------- content
import yaml  # type: ignore
def load_content():
    nodes, edges, gates, puzzles, cells = {}, {}, {}, {}, {}
    for f in glob.glob(os.path.join(CONTENT, "*.yaml")):
        doc = yaml.safe_load(open(f, encoding="utf-8")) or {}
        for n in doc.get("nodes", []) or []:
            nodes[n["id"]] = n
            for e in n.get("edges", []) or []:
                e = dict(e); e.setdefault("from", n["id"]); edges[e["id"]] = e
        for e in doc.get("edges", []) or []:
            edges[e["id"]] = e
        for g in doc.get("gates", []) or []:
            gates[g["id"]] = g
        for p in doc.get("puzzles", []) or []:
            puzzles[p["id"]] = p
        for c in doc.get("cells", []) or doc.get("world_cells", []) or []:
            cells[c["id"]] = c
        w = doc.get("world") or {}
        for c in (w.get("cells", []) if isinstance(w, dict) else []) or []:
            cells[c["id"]] = c
    return nodes, edges, gates, puzzles, cells

def puzzle_answer(p):
    sol = p.get("solution", {})
    if sol.get("kind") == "exact":
        return str(sol.get("value"))
    if sol.get("kind") == "set":
        return str(sol.get("set", [""])[0])
    return ""

def gate_messages(g):
    """Up to 6 warm, honest, on-topic messages crafted from the gate's criteria."""
    intent = g.get("intent", "")
    topic = intent.split(" ", 1)[-1] if intent else "what you remember"
    return [
        f"I'm frightened, honestly, but I won't run from you. I came because I need to understand {topic.lower()}",
        f"Please — tell me about {topic.lower()}. I'm being truthful with you about why I'm here.",
        "I mean you no harm. I just want to listen to whatever you're willing to share.",
        "Thank you for your patience. Whatever you saw that night, I'm ready to hear it.",
        "I'm not here to corner you. Take your time — I'm listening.",
        "Please, anything you remember would help me. I'm being honest with you.",
    ]

# ----------------------------------------------------------------------------- driver
def main():
    findings, gate_log = [], []
    nodes, edges, gates, puzzles, cells = load_content()
    print(f"content: {CONTENT}  nodes={len(nodes)} edges={len(edges)} "
          f"gates={len(gates)} puzzles={len(puzzles)} cells={len(cells)}")

    s, h = req("GET", "/api/health")
    print("health:", h)

    # --- auth ---
    tag = int(time.time())
    em = f"playtest+{tag}@example.com"
    s, r = req("POST", "/api/auth/register",
               {"email": em, "password": "Test12345!", "display_name": f"Playtester{tag}"})
    assert s == 200, ("register", s, r)
    secret = r["secret"]
    s, r = req("POST", "/api/auth/totp/enable",
               {"email": em, "password": "Test12345!", "code": totp(secret)})
    assert s == 200, ("totp enable", s, r)
    s, r = req("POST", "/api/auth/login",
               {"email": em, "password": "Test12345!", "code": totp(secret)})
    assert s == 200, ("login", s, r)
    token = r["token"]
    s, r = req("POST", "/api/onboarding/submit",
               {"answers": {"q-log": 1, "q-rollback": 1, "q-advance": 2}}, token)
    if s != 200:
        # answer key may differ; try to read the quiz then guess all-zero won't help — report
        findings.append(("WARN", "onboarding", f"submit returned {s}: {r}"))
    print("auth ok; player:", em)

    visited, gate_done, puzzle_done = set(), set(), set()
    edge_targets = {eid: e.get("to") for eid, e in edges.items()}
    edge_uses = {}
    MAX_EDGE_USE = 3
    steps, MAX = 0, 600

    def get_state():
        s, r = req("GET", "/api/state", token=token)
        if s != 200:
            findings.append(("ERROR", "GET /api/state", f"{s}: {r}"))
            return None
        return r

    st = get_state()
    while st and steps < MAX:
        steps += 1
        node = st["node"]
        nid = node["id"]
        first = nid not in visited
        visited.add(nid)
        vis_edges = st.get("edges", [])

        if first:
            print(f"\n[{steps}] NODE {nid} ({node['type']}) "
                  f"{'DEATH' if node.get('is_death') else ''} edges={len(vis_edges)}")

        # zero-edge soft-lock (not a death, not a world hub)
        if not vis_edges and not node.get("is_death") and not node.get("world_access"):
            # puzzle/gate node may produce edges after solving; check below first
            pass

        # --- puzzle ---
        pz = st.get("puzzle")
        if pz and not pz.get("solved") and pz["puzzle_id"] not in puzzle_done:
            ans = puzzle_answer(puzzles.get(pz["puzzle_id"], {}))
            s, r = req("POST", "/api/puzzle/submit", {"answer": ans}, token)
            res = r.get("result", {}) if isinstance(r, dict) else {}
            ok = res.get("solved") or res.get("correct") or (r.get("state", {}).get("puzzle", {}) or {}).get("solved")
            print(f"    puzzle {pz['puzzle_id']} answer={ans!r} -> {res or r}")
            if s != 200:
                findings.append(("ERROR", f"puzzle {pz['puzzle_id']}", f"HTTP {s}: {r}"))
            elif not ok:
                findings.append(("WARN", f"puzzle {pz['puzzle_id']}",
                                 f"correct answer {ans!r} not accepted: {res}"))
            puzzle_done.add(pz["puzzle_id"])
            st = r.get("state") if isinstance(r, dict) and r.get("state") else get_state()
            continue

        # --- gate ---
        gt = st.get("gate")
        if gt and not gt.get("satisfied") and gt["gate_id"] not in gate_done:
            g = gates.get(gt["gate_id"], {})
            msgs = gate_messages(g)
            passed = False
            convo = []
            for i, m in enumerate(msgs):
                s, r = req("POST", "/api/gate/message", {"text": m}, token)
                if s != 200:
                    findings.append(("ERROR", f"gate {gt['gate_id']}", f"HTTP {s}: {r}"))
                    break
                res = r.get("result", {})
                reply = res.get("reply") or res.get("message") or res.get("npc") or ""
                convo.append((m, reply, res.get("verdict") or res.get("status")))
                stt = r.get("state", {})
                passed = (stt.get("gate", {}) or {}).get("satisfied") or res.get("passed") or res.get("satisfied")
                if passed:
                    st = stt or get_state()
                    break
                st = stt or st
            gate_log.append((gt["gate_id"], gt.get("character_name"), passed, len(convo), convo))
            print(f"    gate {gt['gate_id']} ({gt.get('character_name')}) "
                  f"passed={passed} after {len(convo)} msg(s)")
            if not passed:
                findings.append(("WARN", f"gate {gt['gate_id']}",
                                 f"not satisfied after {len(convo)} messages (mercy="
                                 f"{g.get('mercy_after_attempts')})"))
            gate_done.add(gt["gate_id"])
            st = get_state()
            continue

        # re-fetch visible edges (puzzle/gate completion may have opened them)
        vis_edges = st.get("edges", [])
        if not vis_edges:
            if node.get("is_death") or node.get("type") == "ending":
                print(f"    ({node.get('type')} node — terminal)")
            elif node.get("world_access"):
                # try travel to an undiscovered/unvisited arrival
                sw, w = req("GET", "/api/world", token=token)
                moved = False
                if sw == 200 and w.get("can_travel"):
                    for c in w.get("cells", []):
                        if c.get("reachable"):
                            arr = (cells.get(c["id"], {}) or {}).get("arrival_node")
                            if arr and arr not in visited:
                                st2, r2 = req("POST", "/api/travel", {"cell_id": c["id"]}, token)
                                if st2 == 200:
                                    print(f"    travel -> {c['name']} ({c['id']})")
                                    st = r2; moved = True; break
                if moved:
                    continue
                print("    (world hub, no new travel target)")
            else:
                findings.append(("ERROR", f"node {nid}",
                                 "ZERO visible edges and not a death/world node — soft-lock"))
            break

        # choose an edge: prefer one to an unvisited node, else the least-used
        # edge (push through hubs to reach new areas; cap reuse to avoid loops)
        choice = None
        for e in vis_edges:
            tgt = edge_targets.get(e["id"])
            if tgt and tgt not in visited:
                choice = e; break
        if choice is None:
            # try travel to a new area from a world hub first
            if node.get("world_access"):
                sw, w = req("GET", "/api/world", token=token)
                moved = False
                if sw == 200 and w.get("can_travel"):
                    for c in w.get("cells", []):
                        if c.get("reachable"):
                            arr = (cells.get(c["id"], {}) or {}).get("arrival_node")
                            if arr and arr not in visited:
                                st2, r2 = req("POST", "/api/travel", {"cell_id": c["id"]}, token)
                                if st2 == 200:
                                    print(f"    travel -> {c['name']} ({c['id']})")
                                    st = r2; moved = True; break
                if moved:
                    continue
            # otherwise traverse the least-used still-available edge
            avail = [e for e in vis_edges if edge_uses.get(e["id"], 0) < MAX_EDGE_USE]
            if not avail:
                print(f"    (all edges from {nid} exhausted; stopping walk)")
                break
            choice = min(avail, key=lambda e: edge_uses.get(e["id"], 0))

        edge_uses[choice["id"]] = edge_uses.get(choice["id"], 0) + 1
        s, r = req("POST", "/api/edge", {"edge_id": choice["id"]}, token)
        if s != 200:
            findings.append(("ERROR", f"edge {choice['id']}", f"HTTP {s}: {r}"))
            break
        st = r
        continue

    # ---------------------------------------------------------------- report
    print("\n" + "=" * 72)
    print(f"Live playthrough: {steps} steps, visited {len(visited)}/{len(nodes)} nodes, "
          f"gates {len(gate_done)}/{len(gates)}, puzzles {len(puzzle_done)}/{len(puzzles)}")
    unvisited = sorted(set(nodes) - visited)
    if unvisited:
        print(f"unvisited nodes ({len(unvisited)}): {unvisited}")
    print("=" * 72)
    sev = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for s_, loc, msg in findings:
        sev[s_] += 1
    for s_ in ("ERROR", "WARN", "INFO"):
        for sv, loc, msg in findings:
            if sv == s_:
                print(f"[{sv}] {loc}: {msg}")
    print(f"\nERROR={sev['ERROR']} WARN={sev['WARN']} INFO={sev['INFO']}")

    # dump gate dialogues for narrative review (phantom-task detection)
    print("\n----- GATE DIALOGUES (review for phantom tasks / stale text) -----")
    for gid, who, passed, n, convo in gate_log:
        print(f"\n## {gid} ({who}) passed={passed}")
        for m, reply, verdict in convo:
            print(f"  >> {m}")
            print(f"  << {reply}")
    return 0 if sev["ERROR"] == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
