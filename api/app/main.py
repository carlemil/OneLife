"""OneLife vertical-slice API.

Auth is simplified to a bearer session token (no password/2FA yet — see
DATA_MODEL.md §players for the real plan). Every mutating endpoint runs inside a
transaction so a failed action leaves no partial state.
"""
import json
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import db, engine, gates, puzzles, llm
from .dsl import evaluate

app = FastAPI(title="OneLife API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    await db.get_pool()


@app.on_event("shutdown")
async def _shutdown():
    await db.close_pool()


async def _session(authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT s.token, s.player_id, s.current_node, s.story_time, s.log_id,
                      p.display_name
               FROM player_sessions s JOIN players p ON p.id = s.player_id
               WHERE s.token=$1""", token)
    if row is None:
        raise HTTPException(401, "invalid token")
    return dict(row)


# --------------------------------------------------------------------------- #
class RegisterBody(BaseModel):
    display_name: str

class EdgeBody(BaseModel):
    edge_id: str

class GateBody(BaseModel):
    text: str

class PuzzleBody(BaseModel):
    answer: str

class RollbackBody(BaseModel):
    to_seq: int


@app.get("/api/health")
async def health():
    return {"ok": True, "using_real_llm": llm.USING_REAL_LLM}


@app.post("/api/auth/register")
async def register(body: RegisterBody):
    name = body.display_name.strip()
    if not name:
        raise HTTPException(400, "display_name required")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM players WHERE display_name=$1", name)
            if exists:
                raise HTTPException(409, "name taken")
            player_id = await conn.fetchval(
                "INSERT INTO players (display_name) VALUES ($1) RETURNING id", name)
            log_id = await conn.fetchval(
                "INSERT INTO game_logs (player_id) VALUES ($1) RETURNING id", player_id)
            entry = await conn.fetchrow(
                "SELECT id FROM story_nodes WHERE is_entry LIMIT 1")
            token = await conn.fetchval(
                """INSERT INTO player_sessions (player_id, current_node, log_id)
                   VALUES ($1,$2,$3) RETURNING token""",
                player_id, entry["id"], log_id)
            # Seq 0: the player wakes at the entry node.
            await conn.execute(
                """INSERT INTO log_entries (log_id, seq, story_time, node_id, summary)
                   VALUES ($1,0,0,$2,'You woke at Killebäckskolan.')""",
                log_id, entry["id"])
            await engine.discover_clues(conn, player_id, log_id, 0, 0)
    return {"token": str(token), "display_name": name}


@app.get("/api/state")
async def state(authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        return await engine.render_state(conn, sess["player_id"], sess)


@app.post("/api/edge")
async def take_edge(body: EdgeBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
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
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await gates.process_message(conn, sess["player_id"], sess, body.text)
        state_after = await engine.render_state(conn, sess["player_id"], sess)
    return {"result": result, "state": state_after}


@app.post("/api/puzzle/submit")
async def puzzle_submit(body: PuzzleBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await puzzles.submit(conn, sess["player_id"], sess, body.answer)
        state_after = await engine.render_state(conn, sess["player_id"], sess)
    return {"result": result, "state": state_after}


@app.post("/api/rollback")
async def do_rollback(body: RollbackBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
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


@app.get("/api/leaderboard")
async def leaderboard(authorization: str | None = Header(default=None)):
    await _session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT display_name, progress FROM leaderboard LIMIT 20")
    return {"rows": [{"display_name": r["display_name"], "progress": r["progress"]} for r in rows]}
