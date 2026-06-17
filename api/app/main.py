"""OneLife vertical-slice API.

Auth is simplified to a bearer session token (no password/2FA yet — see
DATA_MODEL.md §players for the real plan). Every mutating endpoint runs inside a
transaction so a failed action leaves no partial state.
"""
import os
import json
import uuid
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db, engine, gates, puzzles, llm, memory, content, auth, onboarding, atmosphere, security
from .dsl import evaluate

WEB_ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:5173")
SESSION_TTL = "7 days"

app = FastAPI(title="OneLife API")
app.add_middleware(
    CORSMiddleware, allow_origins=[WEB_ORIGIN], allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _client_ip(request: Request) -> str:
    return request.client.host if request and request.client else "unknown"


@app.on_event("startup")
async def _startup():
    if security.INSECURE_DEFAULT:
        print("[security] WARNING: ONELIFE_SECRET_KEY unset — using an insecure dev "
              "key for TOTP encryption. Set ONELIFE_SECRET_KEY for production.")
    pool = await db.get_pool()
    # Load authored content from YAML (idempotent upsert) so `docker compose up`
    # yields a playable game. Validate first; skip seeding on errors rather than
    # crash, leaving whatever content is already in the DB.
    try:
        data, _ = content.load_dir()
        errors, warnings = content.validate(data)
        for w in warnings:
            print(f"[content] WARN {w}")
        if errors:
            for e in errors:
                print(f"[content] ERROR {e}")
            print("[content] validation failed — skipping seed")
        else:
            async with pool.acquire() as conn:
                await content.seed_content(conn, data)
            print(f"[content] seeded {len(data['nodes'])} nodes from {data and 'YAML'}")
    except Exception as e:  # noqa: BLE001
        print(f"[content] seed skipped: {e}")
    # Self-heal any duplicate leaked memories left by pre-dedupe runs.
    async with pool.acquire() as conn:
        await memory.dedupe_existing(conn)


@app.on_event("shutdown")
async def _shutdown():
    await db.close_pool()


async def _session(authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        uuid.UUID(token)  # malformed token → 401, not a 500 from the UUID column
    except ValueError:
        raise HTTPException(401, "invalid token")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT s.token, s.player_id, s.current_node, s.story_time, s.log_id,
                      p.display_name, p.onboarded
               FROM player_sessions s JOIN players p ON p.id = s.player_id
               WHERE s.token=$1 AND (s.expires_at IS NULL OR s.expires_at > now())""",
            token)
    if row is None:
        raise HTTPException(401, "invalid or expired token")
    return dict(row)


def _require_onboarded(sess: dict):
    if not sess.get("onboarded"):
        raise HTTPException(403, "onboarding required")


async def _start_game(conn, sess: dict):
    """Idempotently begin a player's game: create the log and place them at the
    entry node. No-op if they already have a game in progress."""
    if sess.get("log_id"):
        return
    pid = sess["player_id"]
    log_id = await conn.fetchval(
        "INSERT INTO game_logs (player_id) VALUES ($1) RETURNING id", pid)
    entry = await conn.fetchrow("SELECT id FROM story_nodes WHERE is_entry LIMIT 1")
    await conn.execute(
        """INSERT INTO log_entries (log_id, seq, story_time, node_id, summary)
           VALUES ($1,0,0,$2,'You woke at Killebäckskolan.')""", log_id, entry["id"])
    await conn.execute(
        "UPDATE player_sessions SET current_node=$1, log_id=$2 WHERE player_id=$3",
        entry["id"], log_id, pid)
    sess["current_node"] = entry["id"]
    sess["log_id"] = log_id
    await engine.discover_clues(conn, pid, log_id, 0, 0)
    await _reveal_cells_around(conn, pid, entry["id"], 0)


async def _reveal_cells_around(conn, player_id, node_id, seq):
    """Fog-of-war: discover the node's cell + orthogonally adjacent cells."""
    cell = await conn.fetchrow(
        """SELECT w.grid_x, w.grid_y FROM story_nodes n
           JOIN locations l ON l.id = n.location_id
           JOIN world_cells w ON w.id = l.cell_id
           WHERE n.id=$1""", node_id)
    if cell is None:
        return
    near = await conn.fetch(
        "SELECT id FROM world_cells WHERE abs(grid_x-$1)+abs(grid_y-$2) <= 1",
        cell["grid_x"], cell["grid_y"])
    for c in near:
        await conn.execute(
            """INSERT INTO player_cells (player_id, cell_id, found_at_seq)
               VALUES ($1,$2,$3) ON CONFLICT DO NOTHING""", player_id, c["id"], seq)


# --------------------------------------------------------------------------- #
class RegisterBody(BaseModel):
    email: str
    password: str
    display_name: str

class TotpBody(BaseModel):
    email: str
    password: str
    code: str

class LoginBody(BaseModel):
    email: str
    password: str
    code: str = ""

class OnboardingBody(BaseModel):
    answers: dict[str, int]

class EdgeBody(BaseModel):
    edge_id: str

class GateBody(BaseModel):
    text: str

class PuzzleBody(BaseModel):
    answer: str

class RollbackBody(BaseModel):
    to_seq: int

class TravelBody(BaseModel):
    cell_id: str


@app.get("/api/health")
async def health():
    return {"ok": True, "using_real_llm": llm.USING_REAL_LLM}


# ---------- Auth ----------
@app.post("/api/auth/register")
async def register(body: RegisterBody, request: Request):
    if not security.allow(f"register:{_client_ip(request)}", 10, 3600):
        raise HTTPException(429, "too many registrations — try again later")
    email = body.email.strip().lower()
    name = body.display_name.strip()
    if "@" not in email:
        raise HTTPException(400, "valid email required")
    if len(body.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    if not name:
        raise HTTPException(400, "display_name required")
    secret = auth.new_totp_secret()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT 1 FROM players WHERE email=$1", email):
                raise HTTPException(409, "email already registered")
            if await conn.fetchval("SELECT 1 FROM players WHERE display_name=$1", name):
                raise HTTPException(409, "display name taken")
            await conn.execute(
                """INSERT INTO players (email, password_hash, display_name, totp_secret)
                   VALUES ($1,$2,$3,$4)""",
                email, auth.hash_password(body.password), name, security.encrypt(secret))
    uri = auth.totp_uri(secret, email)
    # Account exists but 2FA must be set up before login; hand back the QR.
    return {"otpauth_uri": uri, "secret": secret, "qr_svg": auth.qr_svg(uri)}


@app.post("/api/auth/totp/enable")
async def totp_enable(body: TotpBody, request: Request):
    if not security.allow(f"totp:{_client_ip(request)}", 20, 3600):
        raise HTTPException(429, "too many attempts — try again later")
    email = body.email.strip().lower()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        p = await conn.fetchrow(
            "SELECT id, password_hash, totp_secret FROM players WHERE email=$1", email)
        if p is None or not auth.verify_password(body.password, p["password_hash"]):
            raise HTTPException(401, "invalid email or password")
        if not auth.verify_totp(security.decrypt(p["totp_secret"]), body.code):
            raise HTTPException(400, "invalid authenticator code")
        # Issue one-time recovery codes (shown once, stored hashed).
        codes = auth.generate_recovery_codes()
        async with conn.transaction():
            await conn.execute("UPDATE players SET totp_enabled=TRUE WHERE id=$1", p["id"])
            await conn.execute("DELETE FROM recovery_codes WHERE player_id=$1", p["id"])
            for c in codes:
                await conn.execute(
                    "INSERT INTO recovery_codes (player_id, code_hash) VALUES ($1,$2)",
                    p["id"], auth.hash_password(c))
    return {"ok": True, "recovery_codes": codes}


async def _check_recovery(conn, player_id, code: str) -> bool:
    """Consume a matching unused recovery code; True if one matched."""
    rows = await conn.fetch(
        "SELECT id, code_hash FROM recovery_codes WHERE player_id=$1 AND NOT used", player_id)
    for r in rows:
        if auth.verify_password(code, r["code_hash"]):
            await conn.execute("UPDATE recovery_codes SET used=TRUE WHERE id=$1", r["id"])
            return True
    return False


@app.post("/api/auth/login")
async def login(body: LoginBody, request: Request):
    ip = _client_ip(request)
    email = body.email.strip().lower()
    if not security.allow(f"login:{ip}", 30, 60):
        raise HTTPException(429, "too many requests — slow down")
    if security.locked(f"loginfail:{email}", 5, 300):
        raise HTTPException(429, "too many failed attempts — wait 5 minutes")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        p = await conn.fetchrow(
            """SELECT id, password_hash, totp_secret, totp_enabled, onboarded
               FROM players WHERE email=$1""", email)
        if p is None or not auth.verify_password(body.password, p["password_hash"]):
            security.record_fail(f"loginfail:{email}")
            raise HTTPException(401, "invalid email or password")
        if not p["totp_enabled"]:
            raise HTTPException(403, "two-factor setup not complete")
        code = auth.normalize_code(body.code)
        ok = auth.verify_totp(security.decrypt(p["totp_secret"]), code) \
            or await _check_recovery(conn, p["id"], code)
        if not ok:
            security.record_fail(f"loginfail:{email}")
            raise HTTPException(401, "invalid authenticator or recovery code")
        security.clear_fails(f"loginfail:{email}")
        # One session per player: rotate the token + TTL, preserve game state.
        token = await conn.fetchval(
            f"""INSERT INTO player_sessions (player_id, expires_at)
                VALUES ($1, now() + interval '{SESSION_TTL}')
                ON CONFLICT (player_id)
                DO UPDATE SET token=gen_random_uuid(), created_at=now(),
                  expires_at=now() + interval '{SESSION_TTL}'
                RETURNING token""", p["id"])
    return {"token": str(token), "onboarded": p["onboarded"]}


# ---------- Onboarding (forced manual + quiz) ----------
@app.get("/api/onboarding")
async def get_onboarding(authorization: str | None = Header(default=None)):
    await _session(authorization)
    return {"manual": onboarding.MANUAL, "questions": onboarding.public_questions()}


@app.post("/api/onboarding/submit")
async def submit_onboarding(body: OnboardingBody,
                            authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    passed, score, total = onboarding.grade(body.answers)
    if passed:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE players SET onboarded=TRUE WHERE id=$1", sess["player_id"])
                sess["onboarded"] = True
                await _start_game(conn, sess)
    return {"passed": passed, "score": score, "total": total}


@app.get("/api/state")
async def state(authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        return await engine.render_state(conn, sess["player_id"], sess)


@app.post("/api/edge")
async def take_edge(body: EdgeBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            edge = await conn.fetchrow("SELECT * FROM story_edges WHERE id=$1", body.edge_id)
            if edge is None or edge["from_node"] != sess["current_node"]:
                raise HTTPException(400, "edge not available from current node")
            ctx = await engine.load_context(conn, sess["player_id"], sess["story_time"])
            if not evaluate(json.loads(edge["conditions"]), ctx):
                raise HTTPException(400, "edge conditions not met")
            effects = json.loads(edge["effects"])
            seq, story_time = await engine.apply_action(
                conn, sess["player_id"], sess["log_id"], node_id=edge["to_node"],
                effects=effects, story_time=sess["story_time"], kind="action")
            await conn.execute(
                "UPDATE player_sessions SET current_node=$1, story_time=$2 WHERE token=$3",
                edge["to_node"], story_time, sess["token"])
            sess["current_node"] = edge["to_node"]
            sess["story_time"] = story_time
            await engine.discover_clues(conn, sess["player_id"], sess["log_id"], story_time, seq)
        return await engine.render_state(conn, sess["player_id"], sess)


@app.post("/api/gate/message")
async def gate_message(body: GateBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await gates.process_message(conn, sess["player_id"], sess, body.text)
        state_after = await engine.render_state(conn, sess["player_id"], sess)
    return {"result": result, "state": state_after}


@app.post("/api/puzzle/submit")
async def puzzle_submit(body: PuzzleBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await puzzles.submit(conn, sess["player_id"], sess, body.answer)
        state_after = await engine.render_state(conn, sess["player_id"], sess)
    return {"result": result, "state": state_after}


@app.post("/api/rollback")
async def do_rollback(body: RollbackBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                sess = await engine.rollback(conn, sess["player_id"], sess, body.to_seq)
            except ValueError as e:
                raise HTTPException(400, str(e))
        return await engine.render_state(conn, sess["player_id"], sess)


@app.get("/api/log")
async def get_log(authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT seq, summary, node_id FROM log_entries
               WHERE log_id=$1 AND NOT rolled_back ORDER BY seq""", sess["log_id"])
    return {"entries": [{"seq": r["seq"], "summary": r["summary"],
                         "node_id": r["node_id"]} for r in rows]}


@app.get("/api/memories")
async def memories(character: str = "the-janitor",
                   authorization: str | None = Header(default=None)):
    """Debug/demo view of what an NPC remembers, across all players."""
    await _session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT m.content, m.source, m.story_time, m.voided, p.display_name AS origin
               FROM agent_memories m
               LEFT JOIN players p ON p.id = m.origin_player_id
               WHERE m.character_id=$1
               ORDER BY m.created_at""", character)
    return {"character": character, "memories": [
        {"content": r["content"], "source": r["source"], "story_time": r["story_time"],
         "voided": r["voided"], "origin": r["origin"]} for r in rows]}


async def _current_cell(conn, current_node):
    return await conn.fetchrow(
        """SELECT w.id, w.grid_x, w.grid_y FROM story_nodes n
           JOIN locations l ON l.id = n.location_id
           JOIN world_cells w ON w.id = l.cell_id
           WHERE n.id=$1""", current_node)


def _adjacent(a, b) -> bool:
    return abs(a["grid_x"] - b["grid_x"]) + abs(a["grid_y"] - b["grid_y"]) == 1


@app.get("/api/world")
async def get_world(authorization: str | None = Header(default=None)):
    """The DISCOVERED cells (fog-of-war) + which are currently reachable."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        node = await conn.fetchrow(
            "SELECT world_access FROM story_nodes WHERE id=$1", sess["current_node"])
        cur = await _current_cell(conn, sess["current_node"])
        cells = await conn.fetch(
            """SELECT w.id, w.grid_x, w.grid_y, w.name, w.kind, w.region
               FROM world_cells w
               JOIN player_cells pc ON pc.cell_id = w.id AND pc.player_id = $1
               ORDER BY w.grid_y, w.grid_x""", sess["player_id"])
    can_travel = bool(node and node["world_access"])
    out = []
    for c in cells:
        is_current = cur and c["id"] == cur["id"]
        reachable = can_travel and not is_current and cur is not None and _adjacent(cur, c)
        out.append({"id": c["id"], "grid_x": c["grid_x"], "grid_y": c["grid_y"],
                    "name": c["name"], "kind": c["kind"], "region": c["region"],
                    "reachable": reachable})
    return {"current_cell_id": cur["id"] if cur else None,
            "can_travel": can_travel, "cells": out}


@app.post("/api/travel")
async def travel(body: TravelBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            node = await conn.fetchrow(
                "SELECT world_access FROM story_nodes WHERE id=$1", sess["current_node"])
            if not node or not node["world_access"]:
                raise HTTPException(400, "you can't travel from here")
            cur = await _current_cell(conn, sess["current_node"])
            dest = await conn.fetchrow(
                "SELECT id, grid_x, grid_y, name, arrival_node FROM world_cells WHERE id=$1",
                body.cell_id)
            if dest is None or not dest["arrival_node"]:
                raise HTTPException(404, "no such destination")
            discovered = await conn.fetchval(
                "SELECT 1 FROM player_cells WHERE player_id=$1 AND cell_id=$2",
                sess["player_id"], dest["id"])
            if not discovered:
                raise HTTPException(400, "you don't know the way there yet")
            if cur is None or not _adjacent(cur, dest):
                raise HTTPException(400, "that's too far to travel in one step")
            arrival = dest["arrival_node"]
            seq, story_time = await engine.apply_action(
                conn, sess["player_id"], sess["log_id"], node_id=arrival,
                effects={"progress_points": 5, "log": f"You traveled to {dest['name']}."},
                story_time=sess["story_time"], kind="action")
            await conn.execute(
                "UPDATE player_sessions SET current_node=$1, story_time=$2 WHERE token=$3",
                arrival, story_time, sess["token"])
            sess["current_node"] = arrival
            sess["story_time"] = story_time
            await engine.discover_clues(conn, sess["player_id"], sess["log_id"], story_time, seq)
            await _reveal_cells_around(conn, sess["player_id"], arrival, seq)
        return await engine.render_state(conn, sess["player_id"], sess)


@app.get("/api/spotify/config")
async def spotify_config():
    """Public client id + optional redirect URI for the browser PKCE flow.
    redirect_uri lets you match exactly what Spotify accepts; empty = the client
    uses its own origin + path."""
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    return {"client_id": cid, "configured": bool(cid),
            "redirect_uri": os.environ.get("SPOTIFY_REDIRECT_URI", "").strip()}


@app.get("/api/atmosphere")
async def get_atmosphere(spotify: int = 0,
                         authorization: str | None = Header(default=None)):
    """Image (always) + LLM-picked Spotify soundtrack (when spotify=1) for the
    player's current location, set in southern Sweden, 1992."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        node = await conn.fetchrow(
            "SELECT location_id, media FROM story_nodes WHERE id=$1", sess["current_node"])
        loc = cell = None
        if node and node["location_id"]:
            loc = await conn.fetchrow(
                "SELECT id, name, description, cell_id FROM locations WHERE id=$1",
                node["location_id"])
        if loc and loc["cell_id"]:
            cell = await conn.fetchrow(
                "SELECT region FROM world_cells WHERE id=$1", loc["cell_id"])
        theme = json.loads(node["media"]).get("image_theme", "") if node else ""
        setting = atmosphere.setting_for(loc, cell)
        image_url = await atmosphere.image_for(conn, theme, loc, setting)
    tracks = []
    if spotify and loc is not None:
        tracks = await atmosphere.tracks_for(
            loc["id"], loc["name"], loc["description"], theme, setting)
    return {"image_svg": atmosphere.image_svg(theme), "image_url": image_url,
            "setting": setting, "theme": theme, "tracks": tracks,
            "spotify_configured": atmosphere.SPOTIFY_CONFIGURED}


@app.get("/api/leaderboard")
async def leaderboard(authorization: str | None = Header(default=None)):
    await _session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT display_name, progress FROM leaderboard LIMIT 20")
    return {"rows": [{"display_name": r["display_name"], "progress": r["progress"]} for r in rows]}
