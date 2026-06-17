"""OneLife vertical-slice API.

Auth is simplified to a bearer session token (no password/2FA yet — see
DATA_MODEL.md §players for the real plan). Every mutating endpoint runs inside a
transaction so a failed action leaves no partial state.
"""
import json
import uuid
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db, engine, gates, puzzles, llm, memory, content, auth, onboarding, atmosphere
from .dsl import evaluate

app = FastAPI(title="OneLife API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
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
               WHERE s.token=$1""", token)
    if row is None:
        raise HTTPException(401, "invalid token")
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
async def register(body: RegisterBody):
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
                email, auth.hash_password(body.password), name, secret)
    uri = auth.totp_uri(secret, email)
    # Account exists but 2FA must be set up before login; hand back the QR.
    return {"otpauth_uri": uri, "secret": secret, "qr_svg": auth.qr_svg(uri)}


@app.post("/api/auth/totp/enable")
async def totp_enable(body: TotpBody):
    email = body.email.strip().lower()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        p = await conn.fetchrow(
            "SELECT id, password_hash, totp_secret FROM players WHERE email=$1", email)
        if p is None or not auth.verify_password(body.password, p["password_hash"]):
            raise HTTPException(401, "invalid email or password")
        if not auth.verify_totp(p["totp_secret"], body.code):
            raise HTTPException(400, "invalid authenticator code")
        await conn.execute("UPDATE players SET totp_enabled=TRUE WHERE id=$1", p["id"])
    return {"ok": True}


@app.post("/api/auth/login")
async def login(body: LoginBody):
    email = body.email.strip().lower()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        p = await conn.fetchrow(
            """SELECT id, password_hash, totp_secret, totp_enabled, onboarded
               FROM players WHERE email=$1""", email)
        if p is None or not auth.verify_password(body.password, p["password_hash"]):
            raise HTTPException(401, "invalid email or password")
        if not p["totp_enabled"]:
            raise HTTPException(403, "two-factor setup not complete")
        if not auth.verify_totp(p["totp_secret"], body.code):
            raise HTTPException(401, "invalid authenticator code")
        # One session per player: rotate the token, preserve game state.
        token = await conn.fetchval(
            """INSERT INTO player_sessions (player_id) VALUES ($1)
               ON CONFLICT (player_id)
               DO UPDATE SET token=gen_random_uuid(), created_at=now()
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


@app.get("/api/world")
async def get_world(authorization: str | None = Header(default=None)):
    """The cell grid + the player's current cell (for the world map)."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        cells = await conn.fetch(
            "SELECT id, grid_x, grid_y, name, kind, region FROM world_cells ORDER BY grid_y, grid_x")
        cur = await conn.fetchrow(
            """SELECT l.cell_id FROM story_nodes n
               JOIN locations l ON l.id = n.location_id
               WHERE n.id=$1""", sess["current_node"])
        node = await conn.fetchrow(
            "SELECT world_access FROM story_nodes WHERE id=$1", sess["current_node"])
    return {
        "current_cell_id": cur["cell_id"] if cur else None,
        "can_travel": bool(node and node["world_access"]),
        "cells": [{"id": c["id"], "grid_x": c["grid_x"], "grid_y": c["grid_y"],
                   "name": c["name"], "kind": c["kind"], "region": c["region"]}
                  for c in cells],
    }


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
            cell = await conn.fetchrow(
                "SELECT name, arrival_node FROM world_cells WHERE id=$1", body.cell_id)
            if cell is None or not cell["arrival_node"]:
                raise HTTPException(404, "no such destination")
            arrival = cell["arrival_node"]
            seq, story_time = await engine.apply_action(
                conn, sess["player_id"], sess["log_id"], node_id=arrival,
                effects={"progress_points": 5, "log": f"You traveled to {cell['name']}."},
                story_time=sess["story_time"], kind="action")
            await conn.execute(
                "UPDATE player_sessions SET current_node=$1, story_time=$2 WHERE token=$3",
                arrival, story_time, sess["token"])
            sess["current_node"] = arrival
            sess["story_time"] = story_time
            await engine.discover_clues(conn, sess["player_id"], sess["log_id"], story_time, seq)
        return await engine.render_state(conn, sess["player_id"], sess)


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
    tracks = []
    if spotify and loc is not None:
        tracks = await atmosphere.tracks_for(
            loc["id"], loc["name"], loc["description"], theme, setting)
    return {"image_svg": atmosphere.image_svg(theme), "setting": setting,
            "theme": theme, "tracks": tracks,
            "spotify_configured": atmosphere.SPOTIFY_CONFIGURED}


@app.get("/api/leaderboard")
async def leaderboard(authorization: str | None = Header(default=None)):
    await _session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT display_name, progress FROM leaderboard LIMIT 20")
    return {"rows": [{"display_name": r["display_name"], "progress": r["progress"]} for r in rows]}
