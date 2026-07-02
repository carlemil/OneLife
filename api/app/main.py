"""OneLife vertical-slice API.

Auth is simplified to a bearer session token (no password/2FA yet — see
DATA_MODEL.md §players for the real plan). Every mutating endpoint runs inside a
transaction so a failed action leaves no partial state.
"""
import os
import json
import uuid
import traceback
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from . import db, engine, gates, puzzles, llm, memory, content, content_log, auth, onboarding, atmosphere, security, admin, map_write, gamestate, migrations
from .dsl import evaluate
from .strings import M

# Comma-separated list of allowed browser origins (localhost and the 127.0.0.1
# loopback are different origins, so allow both for local dev).
WEB_ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:5173,http://127.0.0.1:5173")
_origins = [o.strip() for o in WEB_ORIGIN.split(",") if o.strip()]
SESSION_TTL = "7 days"

# Accounts whose email is listed here can use the /api/admin/* export/import.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ONELIFE_ADMIN_EMAILS", "").split(",") if e.strip()}

app = FastAPI(title="OneLife API")
app.add_middleware(
    CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"],
)

# Serve the active game's static assets (the hand-drawn map background images live
# at <content>/images/maps/*.png). Served dynamically from the currently-active
# game's data dir so switching games (admin) swaps the images live, no restart.
@app.get("/content-static/{path:path}")
async def content_static(path: str):
    base = os.path.realpath(gamestate.active_dir())
    full = os.path.realpath(os.path.join(base, path))
    # Path-traversal guard: the resolved file must stay inside the game dir.
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(404, "Not found")
    if not os.path.isfile(full):
        raise HTTPException(404, "Not found")
    return FileResponse(full)


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception):
    """Any unexpected (non-HTTPException) error becomes a clear, generic message
    instead of a bare 'Internal Server Error' / 'HTTP 500' the user can't act on.
    The real traceback is logged server-side for debugging."""
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on our end. Please try again in a moment."},
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
    # FIRST: bring an already-provisioned single-game volume up to the multi-game
    # schema (composite game_id keys, games/player_games, session split). Idempotent
    # and transactional — see migrations.py. Fail-fast so a botched migration surfaces
    # immediately instead of serving errors against a half-migrated schema.
    async with pool.acquire() as conn:
        if await migrations.ensure_multigame(conn):
            print("[migrate] applied multi-game schema migration")
    # Lightweight, idempotent schema migrations for already-provisioned volumes
    # (the db/*.sql init scripts only run on a fresh volume).
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE story_nodes ADD COLUMN IF NOT EXISTS "
            "body_variants JSONB NOT NULL DEFAULT '[]'")
        # Clickable-map navigation: per-node ellipse, per-cell map image + world map
        # ellipse + world-exit hotspot. Idempotent for already-provisioned volumes.
        await conn.execute(
            "ALTER TABLE story_nodes ADD COLUMN IF NOT EXISTS map JSONB NOT NULL DEFAULT '{}'")
        await conn.execute(
            "ALTER TABLE world_cells ADD COLUMN IF NOT EXISTS map JSONB NOT NULL DEFAULT '{}'")
        await conn.execute(
            "ALTER TABLE world_cells ADD COLUMN IF NOT EXISTS map_image TEXT")
        await conn.execute(
            "ALTER TABLE world_cells ADD COLUMN IF NOT EXISTS world_exit JSONB NOT NULL DEFAULT '{}'")
        # The player's free-form clipboard (saved with their profile; never auto-edited).
        await conn.execute(
            "ALTER TABLE players ADD COLUMN IF NOT EXISTS clipboard TEXT NOT NULL DEFAULT ''")
        # Small key/value store for runtime app settings (e.g. the active game).
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # Restore the admin-selected AUTHORING game (the map/graph editors and
        # /content-static act on this dataset; it's independent of each player's
        # active_game_id).
        chosen = await conn.fetchval("SELECT value FROM app_settings WHERE key='active_game'")
        if chosen and gamestate.is_valid_game(chosen):
            gamestate.set_active_game(chosen)
        print(f"[content] authoring game: {gamestate.active_game()}")
    # Seed EVERY game under games/*/data into its own game_id (idempotent upsert), so
    # `docker compose up` yields all playable games. Validate each first; a game that
    # fails validation is skipped (its prior content, if any, stays) without aborting
    # the others.
    for i, (game_id, data_dir) in enumerate(gamestate.all_games_with_data()):
        try:
            data, _ = content.load_dir(data_dir)
            errors, warnings = content.validate(data)
            for w in warnings:
                print(f"[content:{game_id}] WARN {w}")
            if errors:
                for e in errors:
                    print(f"[content:{game_id}] ERROR {e}")
                print(f"[content:{game_id}] validation failed — skipping seed")
                continue
            async with pool.acquire() as conn:
                await content.register_game(conn, game_id, data.get("game", {}), sort_order=i)
                await content.seed_content(conn, data, game_id)
            print(f"[content:{game_id}] seeded {len(data['nodes'])} nodes")
        except Exception as e:  # noqa: BLE001
            print(f"[content:{game_id}] seed skipped: {e}")
    # Node positions: ensure the table exists (and drop the retired event-log tables).
    async with pool.acquire() as conn:
        try:
            await content_log.ensure_tables(conn)
        except Exception as e:  # noqa: BLE001
            print(f"[content] ensure_tables failed: {e}")
    # Self-heal any duplicate leaked memories left by pre-dedupe runs.
    async with pool.acquire() as conn:
        await memory.dedupe_existing(conn)


@app.on_event("shutdown")
async def _shutdown():
    await db.close_pool()


async def _session(authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, M.NOT_SIGNED_IN)
    token = authorization.split(" ", 1)[1].strip()
    try:
        uuid.UUID(token)  # malformed token → 401, not a 500 from the UUID column
    except ValueError:
        raise HTTPException(401, M.SESSION_INVALID)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT s.token, s.player_id, p.active_game_id AS game_id,
                      pg.current_node, COALESCE(pg.story_time, 0) AS story_time, pg.log_id,
                      p.display_name, p.onboarded, p.email
               FROM player_sessions s
               JOIN players p ON p.id = s.player_id
               LEFT JOIN player_games pg
                      ON pg.player_id = p.id AND pg.game_id = p.active_game_id
               WHERE s.token=$1 AND (s.expires_at IS NULL OR s.expires_at > now())""",
            token)
    if row is None:
        raise HTTPException(401, M.SESSION_EXPIRED)
    return dict(row)


def _require_onboarded(sess: dict):
    if not sess.get("onboarded"):
        raise HTTPException(403, M.ONBOARD_FIRST)


def _require_in_game(sess: dict):
    """Gameplay endpoints need an active game selected (the lobby sets it). 409 nudges
    the client back to the lobby rather than failing a game_id=NULL query downstream."""
    if not sess.get("game_id"):
        raise HTTPException(409, M.PICK_GAME)


def _is_admin(sess: dict) -> bool:
    return (sess.get("email") or "").lower() in ADMIN_EMAILS


def _game_name() -> str:
    """The currently-active game's name (admin-switchable; see gamestate)."""
    return gamestate.active_game()


def _require_admin(sess: dict):
    if not _is_admin(sess):
        raise HTTPException(403, M.ADMIN_ONLY)


async def _start_game(conn, sess: dict):
    """Idempotently begin a player's game in their active game: create the log and
    place them at that game's entry node. No-op if this save is already in progress."""
    if sess.get("log_id"):
        return
    pid = sess["player_id"]
    gid = sess["game_id"]
    if not gid:
        raise HTTPException(409, M.PICK_GAME)
    entry = await conn.fetchrow(
        "SELECT id FROM story_nodes WHERE game_id=$1 AND is_entry LIMIT 1", gid)
    if entry is None:
        raise HTTPException(500, M.NO_ENTRY_NODE)
    first_summary = await conn.fetchval(
        "SELECT first_summary FROM games WHERE id=$1", gid) or "You wake."
    log_id = await conn.fetchval(
        "INSERT INTO game_logs (player_id, game_id) VALUES ($1,$2) RETURNING id", pid, gid)
    await conn.execute(
        """INSERT INTO log_entries (log_id, game_id, seq, story_time, node_id, summary, kind)
           VALUES ($1,$2,0,0,$3,$4,'scene')""",
        log_id, gid, entry["id"], first_summary)
    await conn.execute(
        """INSERT INTO player_games (player_id, game_id, current_node, log_id, story_time, last_played_at)
           VALUES ($1,$2,$3,$4,0,now())
           ON CONFLICT (player_id, game_id)
           DO UPDATE SET current_node=EXCLUDED.current_node, log_id=EXCLUDED.log_id,
                         story_time=0, last_played_at=now()""",
        pid, gid, entry["id"], log_id)
    sess["current_node"] = entry["id"]
    sess["log_id"] = log_id
    await engine.discover_clues(conn, pid, gid, log_id, 0, entry["id"])
    # Discover ONLY the start cell — neighbours stay hidden until the player reaches a
    # world-access node (engine.traverse_edge reveals them then, so "the map opens"
    # in play rather than at the very first instant).
    await engine.reveal_cells_around(conn, pid, gid, entry["id"], 0, log_id, 0, neighbors=False)


# --------------------------------------------------------------------------- #
class RegisterBody(BaseModel):
    email: str
    password: str
    display_name: str
    two_factor: bool = True   # opt out to create a password-only account

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

class WalkBody(BaseModel):
    node_id: str

class GateBody(BaseModel):
    text: str

class CompleteBody(BaseModel):
    # Phase 2 of the browser inference broker: the raw completions the player's
    # browser produced for a phase-1 pending_inference, keyed by request id.
    turn_token: str
    completions: dict = {}

class PuzzleBody(BaseModel):
    answer: str
    entry: str = ""   # crossword: which clue is being answered (ignored by other kinds)

class RollbackBody(BaseModel):
    to_seq: int

class GameSwitchBody(BaseModel):
    game: str

class GameSelectBody(BaseModel):
    game_id: str
    mode: str = "continue"   # continue | new

class TravelBody(BaseModel):
    cell_id: str

class MapSaveBody(BaseModel):
    # One ellipse placement from the map editor, written back into the YAML.
    # kind: 'node' (a story node's map) | 'cell' (a cell's world-map ellipse)
    #       | 'world_exit' (a cell's leave-town hotspot).
    kind: str
    id: str
    map: dict   # {x, y, rx, ry} normalized 0..1

class MapScaleBody(BaseModel):
    id: str       # the location's anchor node id
    scale: float

class MapPosBody(BaseModel):
    id: str       # the location's anchor node id
    x: float      # normalized 0..1 overview position
    y: float

class MapEdgeBody(BaseModel):
    from_node: str        # source anchor node id
    to_node: str          # target anchor node id
    label: str = ""       # custom label for both directions; blank = auto "Go to …"
    bidirectional: bool = True   # also create the reverse edge

class ClipboardBody(BaseModel):
    text: str = ""        # the player's free-form clipboard contents

class DataImportBody(BaseModel):
    data: dict
    confirm: str = ""

class ContentMoveBody(BaseModel):
    id: str
    x: float
    y: float

class ContentLayoutBody(BaseModel):
    positions: dict   # {node_id: {"x": .., "y": ..}}

class MapRoadsBody(BaseModel):
    # Computed road splines from "Redraw roads", keyed by location pair, points
    # normalized 0..1 and oriented from->to.
    roads: list   # [{"from": locId, "to": locId, "points": [[x,y],…]}]


@app.get("/api/health")
async def health():
    return {"ok": True, "using_real_llm": llm.USING_REAL_LLM,
            "llm_provider": llm.PROVIDER, "llm_model": llm.BROWSER_MODEL}


# ---------- Auth ----------
@app.post("/api/auth/register")
async def register(body: RegisterBody, request: Request):
    if not security.allow(f"register:{_client_ip(request)}", 10, 3600):
        raise HTTPException(429, M.REGISTER_RATE)
    name = body.display_name.strip()
    if len(body.password) < 8:
        raise HTTPException(400, M.PASSWORD_TOO_SHORT)
    if not name:
        raise HTTPException(400, M.CHARACTER_NAME_REQUIRED)
    # The login identifier (stored in the email column) can be a real email, a plain
    # username, or — if the field is left blank — the character name itself. There is no
    # email-specific behaviour anywhere, so we don't require an "@"; it's just a unique
    # handle the player logs in with.
    login_name = body.email.strip() or name
    email = login_name.lower()
    want_2fa = bool(body.two_factor)
    secret = auth.new_totp_secret() if want_2fa else None
    enc_secret = security.encrypt(secret) if want_2fa else None
    pwd = auth.hash_password(body.password)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                """SELECT id, totp_secret, totp_enabled FROM players WHERE email=$1""",
                email)
            # An account is only reclaimable if it's a half-finished 2FA setup —
            # a secret was issued but never confirmed, so it can never be logged
            # into. (A password-only account has no secret; a finished account is
            # totp_enabled.) Re-registering that dead account heals it instead of
            # leaving the user stuck between a 409 and a 403.
            reclaimable = existing is not None \
                and existing["totp_secret"] is not None and not existing["totp_enabled"]
            if existing is not None and not reclaimable:
                raise HTTPException(409, M.EMAIL_TAKEN)
            if await conn.fetchval(
                    "SELECT 1 FROM players WHERE display_name=$1 AND email<>$2", name, email):
                raise HTTPException(409, M.CHARACTER_NAME_TAKEN)
            if reclaimable:
                await conn.execute(
                    "DELETE FROM recovery_codes WHERE player_id=$1", existing["id"])
                await conn.execute(
                    "DELETE FROM player_sessions WHERE player_id=$1", existing["id"])
                await conn.execute(
                    """UPDATE players SET password_hash=$2, display_name=$3,
                       totp_secret=$4, totp_enabled=FALSE WHERE id=$1""",
                    existing["id"], pwd, name, enc_secret)
            else:
                await conn.execute(
                    """INSERT INTO players (email, password_hash, display_name, totp_secret)
                       VALUES ($1,$2,$3,$4)""",
                    email, pwd, name, enc_secret)
    if not want_2fa:
        # Password-only account — ready to log in immediately.
        return {"two_factor": False, "login_name": login_name}
    uri = auth.totp_uri(secret, email)
    # 2FA must be set up before login; hand back the QR.
    return {"two_factor": True, "otpauth_uri": uri, "secret": secret,
            "qr_svg": auth.qr_svg(uri), "login_name": login_name}


@app.post("/api/auth/totp/enable")
async def totp_enable(body: TotpBody, request: Request):
    if not security.allow(f"totp:{_client_ip(request)}", 20, 3600):
        raise HTTPException(429, M.TOTP_RATE)
    email = body.email.strip().lower()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        p = await conn.fetchrow(
            "SELECT id, password_hash, totp_secret FROM players WHERE email=$1", email)
        if p is None or not auth.verify_password(body.password, p["password_hash"]):
            raise HTTPException(401, M.BAD_CREDENTIALS)
        if not auth.verify_totp(security.decrypt(p["totp_secret"]), body.code):
            raise HTTPException(400, M.BAD_AUTH_CODE)
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
        raise HTTPException(429, M.LOGIN_RATE)
    if security.locked(f"loginfail:{email}", 5, 300):
        raise HTTPException(429, M.LOGIN_LOCKED)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        p = await conn.fetchrow(
            """SELECT id, password_hash, totp_secret, totp_enabled, onboarded
               FROM players WHERE email=$1""", email)
        if p is None or not auth.verify_password(body.password, p["password_hash"]):
            security.record_fail(f"loginfail:{email}")
            raise HTTPException(401, M.BAD_CREDENTIALS)
        if p["totp_secret"] is None:
            pass  # password-only account (2FA opted out) — password is enough
        elif not p["totp_enabled"]:
            raise HTTPException(403, M.TWOFA_INCOMPLETE)
        else:
            code = auth.normalize_code(body.code)
            ok = auth.verify_totp(security.decrypt(p["totp_secret"]), code) \
                or await _check_recovery(conn, p["id"], code)
            if not ok:
                security.record_fail(f"loginfail:{email}")
                raise HTTPException(401, M.BAD_AUTH_OR_RECOVERY)
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
    return {"manual": onboarding.manual(), "questions": onboarding.public_questions()}


@app.post("/api/onboarding/submit")
async def submit_onboarding(body: OnboardingBody,
                            authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    passed, score, total = onboarding.grade(body.answers)
    if passed:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE players SET onboarded=TRUE WHERE id=$1", sess["player_id"])
        # The game itself starts when the player picks one in the lobby
        # (POST /api/games/select), not here.
    return {"passed": passed, "score": score, "total": total}


# ---------- Lobby: pick which game to play (independent save per game) ----------
@app.get("/api/games")
async def lobby_games(authorization: str | None = Header(default=None)):
    """Every available game + this player's per-game saves (Continue vs New) and
    which one is currently active."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        games = await conn.fetch(
            "SELECT id, title, subtitle FROM games ORDER BY sort_order, title")
        saves = await conn.fetch(
            """SELECT pg.game_id, pg.current_node, pg.last_played_at, pg.log_id,
                      n.title AS node_title,
                      COALESCE((SELECT SUM(points) FROM progress_events pe
                                WHERE pe.player_id=pg.player_id AND pe.game_id=pg.game_id
                                  AND NOT pe.voided), 0) AS progress
               FROM player_games pg
               LEFT JOIN story_nodes n ON n.game_id=pg.game_id AND n.id=pg.current_node
               WHERE pg.player_id=$1""", sess["player_id"])
    by = {r["game_id"]: r for r in saves}
    return {
        "active": sess["game_id"],
        "games": [{
            "id": g["id"], "title": g["title"] or g["id"], "subtitle": g["subtitle"],
            "started": g["id"] in by and by[g["id"]]["log_id"] is not None,
            "node_title": by[g["id"]]["node_title"] if g["id"] in by else None,
            "progress": int(by[g["id"]]["progress"]) if g["id"] in by else 0,
            "last_played_at": (by[g["id"]]["last_played_at"].isoformat()
                               if g["id"] in by and by[g["id"]]["last_played_at"] else None),
        } for g in games],
    }


@app.post("/api/games/select")
async def select_game(body: GameSelectBody, authorization: str | None = Header(default=None)):
    """Make a game active for this player and return its state. `mode=new` restarts
    that game's save (this player only); `continue` resumes it (starting it if fresh)."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    game_id = (body.game_id or "").strip()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM games WHERE id=$1", game_id):
            raise HTTPException(404, M.NO_SUCH_GAME)
        await conn.execute(
            "UPDATE players SET active_game_id=$1 WHERE id=$2", game_id, sess["player_id"])
        sess["game_id"] = game_id
        if body.mode == "new":
            await admin.reset_player_game(conn, sess["player_id"], game_id)
        slot = await conn.fetchrow(
            "SELECT current_node, story_time, log_id FROM player_games WHERE player_id=$1 AND game_id=$2",
            sess["player_id"], game_id)
        sess["current_node"] = slot["current_node"] if slot else None
        sess["story_time"] = slot["story_time"] if slot else 0
        sess["log_id"] = slot["log_id"] if slot else None
        if not sess.get("log_id"):
            async with conn.transaction():
                await _start_game(conn, sess)
        return await engine.render_state(conn, sess["player_id"], sess)


@app.post("/api/games/leave")
async def leave_game(authorization: str | None = Header(default=None)):
    """Return to the lobby (non-destructive — the save persists)."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE players SET active_game_id=NULL WHERE id=$1", sess["player_id"])
    return {"ok": True}


@app.get("/api/state")
async def state(authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # A player with an active game should always have it in progress. If the save
        # slot exists but isn't started yet (selected but never bootstrapped), start it
        # instead of rendering a NULL current_node. _start_game is idempotent.
        if not sess.get("current_node") or not sess.get("log_id"):
            async with conn.transaction():
                await _start_game(conn, sess)
        return await engine.render_state(conn, sess["player_id"], sess)


@app.post("/api/clipboard")
async def save_clipboard(body: ClipboardBody, authorization: str | None = Header(default=None)):
    """Persist the player's free-form clipboard on their profile. Player-controlled
    only — the game never writes here automatically."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    text = body.text[:100000]   # generous cap to keep a stray paste from bloating the row
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE players SET clipboard=$2 WHERE id=$1",
                           sess["player_id"], text)
    return {"ok": True}


@app.post("/api/edge")
async def take_edge(body: EdgeBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    gid = sess["game_id"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            edge = await conn.fetchrow(
                "SELECT * FROM story_edges WHERE game_id=$1 AND id=$2", gid, body.edge_id)
            if edge is None or edge["from_node"] != sess["current_node"]:
                raise HTTPException(400, M.OPTION_UNAVAILABLE)
            ctx = await engine.load_context(conn, sess["player_id"], gid, sess["story_time"])
            if not evaluate(json.loads(edge["conditions"]), ctx):
                raise HTTPException(400, M.PATH_NOT_YET)
            move_seq = await engine.traverse_edge(conn, sess, edge)
            # An explicit story choice shifts alignment (navigation via /api/walk and
            # /api/travel does not). Judge the authored label, memoized per edge — unless
            # the edge is marked `no_alignment` (a morally-neutral action, e.g. climbing
            # in/out the broken window), in which case it never moves the player's standing.
            edge_effects = json.loads(edge["effects"]) if edge["effects"] else {}
            label = (edge["label"] or "").strip()
            pending = None
            if label and not edge_effects.get("no_alignment"):
                cached = await _edge_alignment_lookup(conn, gid, edge["id"])
                if cached is None and llm.PROVIDER == "browser":
                    # Take the edge NOW; defer the (uncached) alignment judgement to the
                    # browser. It POSTs the completion to /api/llm/complete, which caches
                    # it and records the shift stamped with this move's seq.
                    req = llm.build_align(label, context="A deliberate story choice.")
                    token = security.sign({"kind": "edge", "gid": gid,
                                           "edge_id": edge["id"], "move_seq": move_seq})
                    pending = {"turn_token": token,
                               "requests": [llm.to_browser_request(req)]}
                else:
                    v = cached if cached is not None else await _edge_alignment(
                        conn, gid, edge["id"], label)
                    await engine.record_alignment(
                        conn, sess["player_id"], gid, move_seq, v["good_evil_delta"],
                        v["law_chaos_delta"], v["reason"], kind="edge")
        state = await engine.render_state(conn, sess["player_id"], sess)
        if pending:
            state["pending_inference"] = pending
        return state


async def _edge_alignment_lookup(conn, game_id: str, edge_id: str) -> dict | None:
    """The cached alignment shift for an edge, or None if not yet judged."""
    row = await conn.fetchrow(
        """SELECT good_evil_delta, law_chaos_delta, reason
           FROM edge_alignment_cache WHERE game_id=$1 AND edge_id=$2""", game_id, edge_id)
    if row:
        return {"good_evil_delta": row["good_evil_delta"],
                "law_chaos_delta": row["law_chaos_delta"], "reason": row["reason"]}
    return None


async def _edge_alignment_store(conn, game_id: str, edge_id: str, v: dict) -> None:
    await conn.execute(
        """INSERT INTO edge_alignment_cache (game_id, edge_id, good_evil_delta, law_chaos_delta, reason)
           VALUES ($1,$2,$3,$4,$5) ON CONFLICT (game_id, edge_id) DO NOTHING""",
        game_id, edge_id, v["good_evil_delta"], v["law_chaos_delta"], v["reason"])


async def _edge_alignment(conn, game_id: str, edge_id: str, label: str) -> dict:
    """The alignment shift of taking an edge, judged from its (static) label ONCE
    and cached per game, so a choice's moral weight is consistent and we don't pay an
    LLM call on every traversal."""
    cached = await _edge_alignment_lookup(conn, game_id, edge_id)
    if cached is not None:
        return cached
    v = await llm.judge_alignment(label, context="A deliberate story choice.")
    await _edge_alignment_store(conn, game_id, edge_id, v)
    return v


@app.post("/api/walk")
async def walk(body: WalkBody, authorization: str | None = Header(default=None)):
    """Walk to any place in the current cell — not only directly-adjacent ones.
    The engine finds the shortest edge-path (through place nodes only) and applies
    each hop, so clicking a far building on the cell map travels there in one go."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    gid = sess["game_id"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            node = await conn.fetchrow(
                "SELECT * FROM story_nodes WHERE game_id=$1 AND id=$2", gid, sess["current_node"])
            paths = await engine.cell_walk_paths(
                conn, sess["player_id"], node, sess["story_time"])
            path = paths.get(body.node_id)
            if not path:
                raise HTTPException(400, M.CANT_WALK_THERE)
            for eid in path:
                edge = await conn.fetchrow(
                    "SELECT * FROM story_edges WHERE game_id=$1 AND id=$2", gid, eid)
                if edge is None or edge["from_node"] != sess["current_node"]:
                    raise HTTPException(400, M.PATH_CLOSED)
                ctx = await engine.load_context(
                    conn, sess["player_id"], gid, sess["story_time"])
                if not evaluate(json.loads(edge["conditions"]), ctx):
                    raise HTTPException(400, M.WAY_BLOCKED)
                await engine.traverse_edge(conn, sess, edge)
        return await engine.render_state(conn, sess["player_id"], sess)


@app.post("/api/gate/message")
async def gate_message(body: GateBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        # Browser mode: don't run the model server-side — hand the prompts to the
        # player's browser (phase 1). It POSTs the completions to /api/llm/complete
        # (phase 2), which parses them and applies effects.
        if llm.PROVIDER == "browser":
            async with conn.transaction():
                pending = await gates.build_turn(conn, sess["player_id"], sess, body.text)
            return {"pending_inference": pending}
        async with conn.transaction():
            result = await gates.process_message(conn, sess["player_id"], sess, body.text)
        state_after = await engine.render_state(conn, sess["player_id"], sess)
    return {"result": result, "state": state_after}


@app.post("/api/llm/complete")
async def llm_complete(body: CompleteBody, authorization: str | None = Header(default=None)):
    """Phase 2 of the browser inference broker: the player's browser ran the phase-1
    prompts on-device and POSTs the raw completions here. We verify the signed token,
    parse the completions server-side (re-validating verdicts), apply effects, and
    return the same shape the originating endpoint returns off-browser."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    try:
        tok = security.unsign(body.turn_token)
    except ValueError:
        raise HTTPException(400, "invalid or expired inference token")
    if tok.get("gid") != sess["game_id"]:
        raise HTTPException(400, "inference token does not match the current game")
    kind = tok.get("kind")
    comps = body.completions or {}
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if kind == "gate":
            async with conn.transaction():
                result = await gates.apply_turn(conn, sess["player_id"], sess, tok, comps)
            state_after = await engine.render_state(conn, sess["player_id"], sess)
            return {"result": result, "state": state_after}
        if kind == "hint":
            async with conn.transaction():
                result = await puzzles.apply_hint(conn, sess["player_id"], sess, tok, comps)
            state_after = await engine.render_state(conn, sess["player_id"], sess)
            return {"result": result, "state": state_after}
        if kind == "edge":
            async with conn.transaction():
                v = llm.parse_align(comps.get("align", ""))
                await _edge_alignment_store(conn, sess["game_id"], tok["edge_id"], v)
                await engine.record_alignment(
                    conn, sess["player_id"], sess["game_id"], tok["move_seq"],
                    v["good_evil_delta"], v["law_chaos_delta"], v["reason"], kind="edge")
            return await engine.render_state(conn, sess["player_id"], sess)
        if kind == "tracks":
            resolved = await atmosphere.tracks_apply(
                tok["location_id"], comps.get("tracks", ""), tok.get("n", 4))
            return {"tracks": resolved}
    raise HTTPException(400, "unknown inference kind")


@app.post("/api/puzzle/submit")
async def puzzle_submit(body: PuzzleBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await puzzles.submit(conn, sess["player_id"], sess, body.answer, body.entry)
        state_after = await engine.render_state(conn, sess["player_id"], sess)
    return {"result": result, "state": state_after}


@app.post("/api/puzzle/hint")
async def puzzle_hint(authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if llm.PROVIDER == "browser":
            async with conn.transaction():
                out = await puzzles.build_hint_request(conn, sess["player_id"], sess)
            if "pending_inference" in out:
                return {"pending_inference": out["pending_inference"]}
            state_after = await engine.render_state(conn, sess["player_id"], sess)
            return {"result": out["result"], "state": state_after}
        async with conn.transaction():
            result = await puzzles.request_hint(conn, sess["player_id"], sess)
        state_after = await engine.render_state(conn, sess["player_id"], sess)
    return {"result": result, "state": state_after}


@app.post("/api/rollback")
async def do_rollback(body: RollbackBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
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
    _require_in_game(sess)
    gid = sess["game_id"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT seq, summary, node_id, kind FROM log_entries
               WHERE log_id=$1 AND NOT rolled_back ORDER BY seq""", sess["log_id"])
        # Weave the NPC conversation into the same flow. Gate messages are stamped
        # with the seq they happened at and pruned on rollback, so they stay in
        # step with the log beats. Resolve each NPC's name the way the live view
        # does (a name-withholder stays a role descriptor until it's learned).
        msgs = await conn.fetch(
            """SELECT m.seq, m.role, m.content, m.created_at, g.character_id
               FROM gate_messages m JOIN dialogue_gates g ON g.id = m.gate_id AND g.game_id = m.game_id
               WHERE m.player_id=$1 AND m.game_id=$2 ORDER BY m.seq, m.created_at""",
            sess["player_id"], gid)
        ctx = await engine.load_context(conn, sess["player_id"], gid, sess["story_time"])
        chars = {c["id"]: c for c in await conn.fetch(
            "SELECT id, name, reveal_name FROM characters WHERE game_id=$1", gid)}

    entries = [{"seq": r["seq"], "ord": 0, "summary": r["summary"],
                "node_id": r["node_id"], "kind": r["kind"]} for r in rows]
    for m in msgs:
        if m["role"] == "player":
            speaker = "You"
        else:
            c = chars.get(m["character_id"])
            speaker = engine._resolve_name(
                c, m["character_id"] in ctx.known_names)[0] if c else "NPC"
        entries.append({"seq": m["seq"], "ord": 1, "summary": m["content"],
                        "node_id": None, "kind": "dialogue", "speaker": speaker})
    # Stable sort keeps each turn's player→NPC order (already created_at-ordered)
    # and places a beat's dialogue right after the beat itself.
    entries.sort(key=lambda e: (e["seq"], e["ord"]))
    return {"entries": entries}


@app.get("/api/alignment")
async def get_alignment(authorization: str | None = Header(default=None)):
    """The caller's current D&D alignment + the full drift history (the chart's
    fading trail). Only non-rolled-back rows exist (rollback deletes them)."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    gid = sess["game_id"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        ge, lc = await engine.current_alignment(conn, sess["player_id"], gid)
        rows = await conn.fetch(
            """SELECT created_seq AS seq, good_evil, law_chaos, reason, kind
               FROM player_alignment_events WHERE player_id=$1 AND game_id=$2 ORDER BY id""",
            sess["player_id"], gid)
    return {
        "current": {"good_evil": ge, "law_chaos": lc,
                    "label": engine.alignment_label(ge, lc)},
        "history": [{"seq": r["seq"], "good_evil": r["good_evil"],
                     "law_chaos": r["law_chaos"], "reason": r["reason"],
                     "kind": r["kind"]} for r in rows],
    }


@app.get("/api/memories")
async def memories(character: str = "the-janitor",
                   authorization: str | None = Header(default=None)):
    """Debug/demo view of what an NPC remembers, across all players."""
    sess = await _session(authorization)
    _require_in_game(sess)
    gid = sess["game_id"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT m.content, m.source, m.story_time, m.voided, p.display_name AS origin
               FROM agent_memories m
               LEFT JOIN players p ON p.id = m.origin_player_id
               WHERE m.game_id=$1 AND m.character_id=$2
               ORDER BY m.created_at""", gid, character)
    return {"character": character, "memories": [
        {"content": r["content"], "source": r["source"], "story_time": r["story_time"],
         "voided": r["voided"], "origin": r["origin"]} for r in rows]}


async def _current_cell(conn, game_id, current_node):
    return await conn.fetchrow(
        """SELECT w.id, w.grid_x, w.grid_y FROM story_nodes n
           JOIN locations l ON l.id = n.location_id AND l.game_id = n.game_id
           JOIN world_cells w ON w.id = l.cell_id AND w.game_id = l.game_id
           WHERE n.game_id=$1 AND n.id=$2""", game_id, current_node)


def _adjacent(a, b) -> bool:
    return abs(a["grid_x"] - b["grid_x"]) + abs(a["grid_y"] - b["grid_y"]) == 1


def _cell_ellipse(c, minx, maxx, miny, maxy) -> dict:
    """A cell's ellipse on the WORLD map: explicit `map` if set, else derived from
    its grid position (normalized over all cells' extents). Normalized 0..1."""
    m = c["map"] if isinstance(c["map"], dict) else (json.loads(c["map"]) if c["map"] else {})
    if m and m.get("x") is not None:
        return {"x": float(m["x"]), "y": float(m["y"]),
                "rx": float(m.get("rx", 0.09)), "ry": float(m.get("ry", 0.07))}
    nx = 0.5 if maxx == minx else (c["grid_x"] - minx) / (maxx - minx)
    ny = 0.5 if maxy == miny else (c["grid_y"] - miny) / (maxy - miny)
    return {"x": round(0.15 + nx * 0.70, 4), "y": round(0.15 + ny * 0.70, 4),
            "rx": 0.09, "ry": 0.07}


@app.get("/api/world")
async def get_world(authorization: str | None = Header(default=None)):
    """The DISCOVERED cells (fog-of-war) as world-map ellipses + which are
    currently reachable, plus the roads (adjacent discovered pairs)."""
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    gid = sess["game_id"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        node = await conn.fetchrow(
            "SELECT world_access FROM story_nodes WHERE game_id=$1 AND id=$2",
            gid, sess["current_node"])
        cur = await _current_cell(conn, gid, sess["current_node"])
        allcells = await conn.fetch("SELECT grid_x, grid_y FROM world_cells WHERE game_id=$1", gid)
        cells = await conn.fetch(
            """SELECT w.id, w.grid_x, w.grid_y, w.name, w.kind, w.region, w.map
               FROM world_cells w
               JOIN player_cells pc ON pc.cell_id = w.id AND pc.game_id = w.game_id
                    AND pc.player_id = $1
               WHERE w.game_id = $2
               ORDER BY w.grid_y, w.grid_x""", sess["player_id"], gid)
    can_travel = bool(node and node["world_access"])
    xs = [c["grid_x"] for c in allcells] or [0]
    ys = [c["grid_y"] for c in allcells] or [0]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    out = []
    for c in cells:
        is_current = cur and c["id"] == cur["id"]
        reachable = can_travel and not is_current and cur is not None and _adjacent(cur, c)
        out.append({"id": c["id"], "grid_x": c["grid_x"], "grid_y": c["grid_y"],
                    "name": c["name"], "kind": c["kind"], "region": c["region"],
                    "reachable": reachable, "current": bool(is_current),
                    "map": _cell_ellipse(c, minx, maxx, miny, maxy)})
    roads = []
    for i in range(len(out)):
        for j in range(i + 1, len(out)):
            if _adjacent(out[i], out[j]):
                roads.append({"from": out[i]["id"], "to": out[j]["id"]})
    return {"current_cell_id": cur["id"] if cur else None,
            "can_travel": can_travel, "cells": out, "roads": roads,
            "world_image": "images/maps/world.png"}


@app.post("/api/travel")
async def travel(body: TravelBody, authorization: str | None = Header(default=None)):
    sess = await _session(authorization)
    _require_onboarded(sess)
    _require_in_game(sess)
    gid = sess["game_id"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            node = await conn.fetchrow(
                "SELECT world_access FROM story_nodes WHERE game_id=$1 AND id=$2",
                gid, sess["current_node"])
            if not node or not node["world_access"]:
                raise HTTPException(400, M.CANT_TRAVEL_HERE)
            cur = await _current_cell(conn, gid, sess["current_node"])
            dest = await conn.fetchrow(
                "SELECT id, grid_x, grid_y, name, arrival_node FROM world_cells WHERE game_id=$1 AND id=$2",
                gid, body.cell_id)
            if dest is None or not dest["arrival_node"]:
                raise HTTPException(404, M.NO_DESTINATION)
            discovered = await conn.fetchval(
                "SELECT 1 FROM player_cells WHERE player_id=$1 AND game_id=$2 AND cell_id=$3",
                sess["player_id"], gid, dest["id"])
            if not discovered:
                raise HTTPException(400, M.DONT_KNOW_WAY)
            if cur is None or not _adjacent(cur, dest):
                raise HTTPException(400, M.TOO_FAR)
            arrival = dest["arrival_node"]
            seq, story_time = await engine.apply_action(
                conn, sess["player_id"], gid, sess["log_id"], node_id=arrival,
                effects={"progress_points": 5, "log": f"You traveled to {dest['name']}."},
                story_time=sess["story_time"], kind="action")
            await conn.execute(
                """UPDATE player_games SET current_node=$1, story_time=$2, last_played_at=now()
                   WHERE player_id=$3 AND game_id=$4""",
                arrival, story_time, sess["player_id"], gid)
            sess["current_node"] = arrival
            sess["story_time"] = story_time
            await engine.discover_clues(
                conn, sess["player_id"], gid, sess["log_id"], story_time, arrival)
            await engine.reveal_cells_around(
                conn, sess["player_id"], gid, arrival, seq, sess["log_id"], story_time)
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
    _require_in_game(sess)
    gid = sess["game_id"]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        node = await conn.fetchrow(
            "SELECT location_id, media, body FROM story_nodes WHERE game_id=$1 AND id=$2",
            gid, sess["current_node"])
        loc = cell = None
        if node and node["location_id"]:
            loc = await conn.fetchrow(
                "SELECT id, name, description, cell_id FROM locations WHERE game_id=$1 AND id=$2",
                gid, node["location_id"])
        if loc and loc["cell_id"]:
            cell = await conn.fetchrow(
                "SELECT region FROM world_cells WHERE game_id=$1 AND id=$2", gid, loc["cell_id"])
        media = json.loads(node["media"]) if node else {}
        theme = media.get("image_theme", "")
        setting = atmosphere.setting_for(loc, cell, gid)
        image_url = await atmosphere.image_for(
            conn, gid, theme, loc, setting,
            real_place=media.get("real_place"), reference=media.get("reference_image"),
            scene_text=node["body"] if node else None)
    tracks = []
    pending = None
    if spotify and loc is not None:
        if llm.PROVIDER == "browser":
            cached = atmosphere.tracks_cached(loc["id"])
            if cached is not None:
                tracks = cached
            else:
                # Image is ready now; let the browser pick the soundtrack (phase 1) and
                # POST it to /api/llm/complete (phase 2), which resolves it via Spotify.
                req = atmosphere.tracks_request(
                    loc["name"], loc["description"], theme, setting)
                token = security.sign({"kind": "tracks", "location_id": loc["id"], "n": 4})
                pending = {"turn_token": token,
                           "requests": [llm.to_browser_request(req)]}
        else:
            tracks = await atmosphere.tracks_for(
                loc["id"], loc["name"], loc["description"], theme, setting)
    payload = {"image_svg": atmosphere.image_svg(theme), "image_url": image_url,
               "setting": setting, "theme": theme, "tracks": tracks,
               "spotify_configured": atmosphere.SPOTIFY_CONFIGURED}
    if pending:
        payload["pending_inference"] = pending
    return payload


# ---------- Admin: export / import (email-allowlisted) ----------
async def _admin_session(authorization):
    sess = await _session(authorization)
    _require_admin(sess)
    return sess


@app.get("/api/admin/me")
async def admin_me(authorization: str | None = Header(default=None)):
    return {"is_admin": _is_admin(await _session(authorization)), "game": _game_name()}


# ---------- Admin: switch the active game (re-seed from another games/<G>/data) ----------
@app.get("/api/admin/games")
async def admin_games(authorization: str | None = Header(default=None)):
    """Every game folder under games/ that has content, plus which one is active."""
    await _admin_session(authorization)
    return {"games": gamestate.list_games(), "active": gamestate.active_game()}


@app.post("/api/admin/games/switch")
async def admin_games_switch(body: GameSwitchBody,
                             authorization: str | None = Header(default=None)):
    """Switch the admin AUTHORING game — which dataset the map/graph editors and
    /content-static act on. This is independent of what players play (each player
    picks their own game in the lobby) and never touches player progress; it just
    re-seeds the chosen game's content and remembers the choice across restarts."""
    await _admin_session(authorization)
    game = (body.game or "").strip()
    if not gamestate.is_valid_game(game):
        raise HTTPException(404, f"No game '{game}' with content under games/.")
    data, _ = content.load_dir(gamestate.data_dir_for(game))
    errors, warnings = content.validate(data)
    if errors:
        raise HTTPException(400, "Cannot switch — content has errors: " + "; ".join(errors[:5]))
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await content.register_game(conn, game, data.get("game", {}))
        skipped = await content.seed_content(conn, data, game)
        gamestate.set_active_game(game)
        await conn.execute(
            """INSERT INTO app_settings (key, value) VALUES ('active_game', $1)
               ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", game)
    return {"ok": True, "game": game, "nodes": len(data["nodes"]),
            "warnings": warnings, "skipped": skipped}


# ---------- Admin: clickable-map editor (writes ellipse positions back to YAML) ----------
@app.get("/api/admin/map/all")
async def admin_map_all(authorization: str | None = Header(default=None)):
    """Every map (world + one per cell) with its background image and the items the
    author can place: cell maps list their nodes (+ a world-exit hotspot); the world
    map lists the cells. Coords are explicit `map` values where set, else a derived
    default — so the editor always has something to drag."""
    await _admin_session(authorization)
    gid = gamestate.active_game()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        cells = await conn.fetch(
            "SELECT id, name, grid_x, grid_y, map, map_image, world_exit "
            "FROM world_cells WHERE game_id=$1 ORDER BY grid_y, grid_x", gid)
        nodes = await conn.fetch(
            """SELECT n.id, n.title, n.type, n.map, l.cell_id
               FROM story_nodes n JOIN locations l ON l.id = n.location_id AND l.game_id = n.game_id
               WHERE n.game_id=$1 AND l.cell_id IS NOT NULL AND n.type <> 'death' ORDER BY n.id""", gid)
    xs = [c["grid_x"] for c in cells] or [0]
    ys = [c["grid_y"] for c in cells] or [0]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    # World map: one ellipse per cell.
    world_items = [{"kind": "cell", "id": c["id"], "title": c["name"],
                    "map": _cell_ellipse(c, minx, maxx, miny, maxy)} for c in cells]
    maps = [{"id": "world", "title": "World", "image": "images/maps/world.png",
             "items": world_items}]
    # Cell maps: their placeable nodes (auto-laid-out where unplaced) + world-exit.
    for c in cells:
        cnodes = [n for n in nodes if n["cell_id"] == c["id"]
                  and (engine._jmap(n["map"]).get("x") is not None
                       or n["type"] in engine._PLACE_TYPES)]
        auto = engine._auto_layout(
            [n["id"] for n in cnodes if engine._jmap(n["map"]).get("x") is None])
        items = []
        for n in cnodes:
            m = engine._jmap(n["map"])
            xy = ({"x": float(m["x"]), "y": float(m["y"]),
                   "rx": float(m.get("rx", 0.05)), "ry": float(m.get("ry", 0.045))}
                  if m.get("x") is not None else auto[n["id"]])
            items.append({"kind": "node", "id": n["id"],
                          "title": n["title"] or n["id"], "map": xy})
        we = engine._jmap(c["world_exit"])
        if we.get("x") is None:
            we = {"x": 0.92, "y": 0.92, "rx": 0.06, "ry": 0.05}
        items.append({"kind": "world_exit", "id": c["id"], "title": "↪ World map",
                      "map": {"x": float(we["x"]), "y": float(we["y"]),
                              "rx": float(we.get("rx", 0.06)), "ry": float(we.get("ry", 0.05))}})
        maps.append({"id": c["id"], "title": c["name"],
                     "image": c["map_image"] or f"images/maps/{c['id']}.png", "items": items})
    return {"maps": maps}


@app.post("/api/admin/map/save")
async def admin_map_save(body: MapSaveBody, authorization: str | None = Header(default=None)):
    """Write one ellipse placement back into the source YAML (comment-preserving),
    then re-seed from the files so the live game reflects it immediately."""
    await _admin_session(authorization)
    m = body.map or {}
    try:
        clean = {"x": round(float(m["x"]), 4), "y": round(float(m["y"]), 4),
                 "rx": round(float(m.get("rx", 0.05)), 4), "ry": round(float(m.get("ry", 0.045)), 4)}
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "map needs numeric x, y, rx, ry")
    try:
        if body.kind == "node":
            map_write.set_node_map(body.id, clean)
        elif body.kind == "cell":
            map_write.set_cell_field(body.id, "map", clean)
        elif body.kind == "world_exit":
            map_write.set_cell_field(body.id, "world_exit", clean)
        else:
            raise HTTPException(400, f"unknown kind {body.kind}")
    except FileNotFoundError:
        raise HTTPException(404, f"no YAML defines {body.kind} {body.id}")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"could not write YAML: {e}")
    # Re-seed from the just-written files so the change is live without a restart.
    data, _ = content.load_dir()
    errors, _ = content.validate(data)
    if errors:
        raise HTTPException(400, "; ".join(errors[:5]))
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await content.seed_content(conn, data, gamestate.active_game())
    return {"ok": True}


@app.post("/api/admin/map/scale")
async def admin_map_scale(body: MapScaleBody, authorization: str | None = Header(default=None)):
    """Set one icon's scale factor (story_nodes.map.scale), written back to the YAML
    (preserving any x/y/rx/ry) and mirrored into the live DB so the map updates without
    a full re-seed."""
    await _admin_session(authorization)
    gid = gamestate.active_game()
    scale = round(max(0.1, min(5.0, float(body.scale))), 3)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT map FROM story_nodes WHERE game_id=$1 AND id=$2", gid, body.id)
        if row is None:
            raise HTTPException(404, f"no such node {body.id}")
        cur = row["map"]
        cur = json.loads(cur) if isinstance(cur, str) else (dict(cur) if cur else {})
        cur["scale"] = scale
        try:
            map_write.set_node_map(body.id, cur)
        except FileNotFoundError:
            raise HTTPException(404, f"no YAML defines node {body.id}")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"could not write YAML: {e}")
        await conn.execute("UPDATE story_nodes SET map=$3::jsonb WHERE game_id=$1 AND id=$2",
                           gid, body.id, json.dumps(cur))
    return {"ok": True, "scale": scale}


@app.post("/api/admin/map/pos")
async def admin_map_pos(body: MapPosBody, authorization: str | None = Header(default=None)):
    """Set one icon's STABLE overview position (story_nodes.map.x/y, normalized 0..1),
    written to the YAML (preserving any scale/rx/ry) and mirrored into the live DB so the
    map reflects it without a full re-seed. This is the only thing dragging a node saves —
    graph pixel positions are left alone, so no other icon ever moves."""
    await _admin_session(authorization)
    gid = gamestate.active_game()
    x = round(max(0.0, min(1.0, float(body.x))), 4)
    y = round(max(0.0, min(1.0, float(body.y))), 4)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT map FROM story_nodes WHERE game_id=$1 AND id=$2", gid, body.id)
        if row is None:
            raise HTTPException(404, f"no such node {body.id}")
        cur = row["map"]
        cur = json.loads(cur) if isinstance(cur, str) else (dict(cur) if cur else {})
        cur["x"], cur["y"] = x, y
        try:
            map_write.set_node_map(body.id, cur)
        except FileNotFoundError:
            raise HTTPException(404, f"no YAML defines node {body.id}")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"could not write YAML: {e}")
        await conn.execute("UPDATE story_nodes SET map=$3::jsonb WHERE game_id=$1 AND id=$2",
                           gid, body.id, json.dumps(cur))
    return {"ok": True, "x": x, "y": y}


@app.post("/api/admin/map/edge")
async def admin_map_edge(body: MapEdgeBody, authorization: str | None = Header(default=None)):
    """Add an edge between two places drawn in the map editor (both directions unless
    bidirectional is false), write it into the standalone `edges:` YAML, then re-seed so
    the road shows live. An already-existing direction is left untouched (no duplicate)."""
    await _admin_session(authorization)
    gid = gamestate.active_game()
    if body.from_node == body.to_node:
        raise HTTPException(400, "cannot connect a place to itself")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title FROM story_nodes WHERE game_id=$1 AND id = ANY($2::text[])",
            gid, [body.from_node, body.to_node])
        nmap = {r["id"]: (r["title"] or r["id"]) for r in rows}
        if body.from_node not in nmap or body.to_node not in nmap:
            raise HTTPException(404, "unknown node")
        existing_ids = {r["id"] for r in await conn.fetch(
            "SELECT id FROM story_edges WHERE game_id=$1", gid)}
        existing_pairs = {(r["from_node"], r["to_node"])
                          for r in await conn.fetch(
                              "SELECT from_node, to_node FROM story_edges WHERE game_id=$1", gid)}

    label = (body.label or "").strip()
    directions = [(body.from_node, body.to_node)]
    if body.bidirectional:
        directions.append((body.to_node, body.from_node))

    created = []
    try:
        for a, b in directions:
            if (a, b) in existing_pairs:
                continue                       # don't duplicate a direction that exists
            base, eid, k = f"e-{a}-{b}", f"e-{a}-{b}", 2
            while eid in existing_ids:
                eid = f"{base}-{k}"; k += 1
            existing_ids.add(eid)
            lbl = label or f"Go to {nmap[b]}"
            map_write.add_standalone_edge(
                {"id": eid, "from": a, "to": b, "label": lbl,
                 "effects": {"log": f"You went to {nmap[b]}."}, "sort_order": 9})
            created.append(eid)
    except FileNotFoundError as e:
        raise HTTPException(500, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"could not write YAML: {e}")

    if not created:
        return {"ok": True, "created": [], "note": "edge(s) already existed"}
    data, _ = content.load_dir()
    errors, _ = content.validate(data)
    if errors:
        raise HTTPException(400, "; ".join(errors[:5]))
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await content.seed_content(conn, data, gid)
    return {"ok": True, "created": created}


@app.get("/api/admin/map/overview")
async def admin_map_overview(authorization: str | None = Header(default=None)):
    """The whole overview map (all icons + roads, no fog) for the road editor."""
    await _admin_session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        return await engine.map_overview(conn, gamestate.active_game())


@app.post("/api/admin/map/roads")
async def admin_map_roads(body: MapRoadsBody,
                          authorization: str | None = Header(default=None)):
    """Persist computed road splines onto the edges they belong to: each location-pair
    route is written to every story edge between the two places' anchor nodes (oriented
    to that edge's direction) — in the YAML (source of truth) and the DB."""
    await _admin_session(authorization)
    gid = gamestate.active_game()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        anchor = await engine.map_road_anchors(conn, gid)
        written, missing = 0, 0
        for road in body.roads:
            fl, tl = road.get("from"), road.get("to")
            points = road.get("points") or []
            na, nb = anchor.get(fl), anchor.get(tl)
            if not na or not nb:
                continue
            erows = await conn.fetch(
                """SELECT id, from_node FROM story_edges
                   WHERE game_id=$3 AND ((from_node=$1 AND to_node=$2) OR (from_node=$2 AND to_node=$1))""",
                na, nb, gid)
            if not erows:
                missing += 1
                continue
            for e in erows:
                pts = points if e["from_node"] == na else list(reversed(points))
                await conn.execute("UPDATE story_edges SET road=$3::jsonb WHERE game_id=$1 AND id=$2",
                                   gid, e["id"], json.dumps(pts))
                try:
                    map_write.set_edge_road(e["id"], pts)
                    written += 1
                except FileNotFoundError:
                    pass   # inter-cell lane with no authored edge — DB-only is fine
    return {"ok": True, "edges_written": written, "pairs_without_edge": missing}


@app.get("/api/admin/content/export")
async def admin_content_export(authorization: str | None = Header(default=None)):
    await _admin_session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        data = await content.export_content(conn, gamestate.active_game())
    import yaml
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return {"filename": "content-export.yaml", "body": body}


# ---------- Admin: read-only content graph + node positioning ----------
# Authoring lives in the YAML files (the single source of truth); the in-app graph
# is a read-only view whose only mutation is dragging a node, which writes its
# position back into the YAML (see /content/move and /content/layout below).
@app.get("/api/admin/content/all")
async def admin_content_all(authorization: str | None = Header(default=None)):
    """The full authored set (+ node positions) — the read-only graph loads it once."""
    await _admin_session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        return await content_log.current(conn, gamestate.active_game())


async def _persist_pos(conn, game_id, node_id: str, x, y) -> None:
    """Write a node's graph position into the authored YAML (the source of truth)
    and mirror it into the node_positions cache the map reads."""
    map_write.set_node_pos(node_id, x, y)
    await conn.execute(
        """INSERT INTO node_positions (game_id,node_id,x,y) VALUES ($1,$2,$3,$4)
           ON CONFLICT (game_id,node_id) DO UPDATE SET x=EXCLUDED.x, y=EXCLUDED.y""",
        game_id, node_id, float(x), float(y))


@app.post("/api/admin/content/move")
async def admin_content_move(body: ContentMoveBody,
                             authorization: str | None = Header(default=None)):
    """Persist a node's graph position straight into its YAML file + the cache."""
    await _admin_session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await _persist_pos(conn, gamestate.active_game(), body.id, body.x, body.y)
    return {"ok": True}


@app.post("/api/admin/content/layout")
async def admin_content_layout(body: ContentLayoutBody,
                               authorization: str | None = Header(default=None)):
    """Persist a whole-graph re-layout into the YAML files + the cache."""
    await _admin_session(authorization)
    gid = gamestate.active_game()
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        for nid, p in body.positions.items():
            await _persist_pos(conn, gid, nid, p["x"], p["y"])
    return {"ok": True}


@app.get("/api/admin/db/export")
async def admin_db_export(authorization: str | None = Header(default=None)):
    await _admin_session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        data = await admin.export_all(conn)
    return {"filename": "onelife-db-export.json", "body": json.dumps(data)}


@app.post("/api/admin/db/import")
async def admin_db_import(body: DataImportBody,
                          authorization: str | None = Header(default=None)):
    await _admin_session(authorization)
    if body.confirm != "REPLACE":
        raise HTTPException(400, "type REPLACE to confirm a destructive import")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await admin.import_all(conn, body.data)
    return {"ok": True}


@app.get("/api/admin/players")
async def admin_players(authorization: str | None = Header(default=None)):
    await _admin_session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        return {"players": await admin.list_players(conn)}


@app.get("/api/admin/player/{player_id}/export")
async def admin_player_export(player_id: str,
                              authorization: str | None = Header(default=None)):
    await _admin_session(authorization)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        save = await admin.export_player(conn, player_id)
    if not save:
        raise HTTPException(404, "no such player")
    name = (save["player"]["display_name"] or "player").replace(" ", "_")
    return {"filename": f"onelife-save-{name}.json", "body": json.dumps(save)}


@app.post("/api/admin/player/import")
async def admin_player_import(body: DataImportBody,
                              authorization: str | None = Header(default=None)):
    await _admin_session(authorization)
    if body.confirm != "REPLACE":
        raise HTTPException(400, "type REPLACE to confirm a destructive import")
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        try:
            await admin.import_player(conn, body.data)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/leaderboard")
async def leaderboard(offset: int = 0, limit: int = 20, q: str = "", around: int = 0,
                      authorization: str | None = Header(default=None)):
    """Ranked leaderboard with the caller's own position. `q` searches names;
    `around=N` returns the caller +/- N neighbours; otherwise the window starting
    at rank `offset`+1 (for jump-to-position)."""
    sess = await _session(authorization)
    _require_in_game(sess)
    gid = sess["game_id"]
    offset = max(0, offset)
    limit = max(1, min(limit, 100))
    pid = sess["player_id"]
    # Per-game board: $1 is the game being ranked. `completed` = the player reached an
    # ending node on their live (non-rolled-back) timeline IN THIS GAME; rollback past
    # the ending un-completes it, consistent with the log invariant.
    cte = ("WITH done AS (SELECT DISTINCT g.player_id FROM log_entries le "
           "  JOIN game_logs g ON g.id = le.log_id "
           "  WHERE NOT le.rolled_back AND g.game_id=$1 "
           "    AND le.node_id IN (SELECT id FROM story_nodes WHERE game_id=$1 AND type='ending')), "
           # Each player's current alignment in this game = their latest (non-rolled-back)
           # event; rollback deletes events, so the newest row is always the live standing.
           "align AS (SELECT DISTINCT ON (player_id) player_id, good_evil, law_chaos "
           "  FROM player_alignment_events WHERE game_id=$1 ORDER BY player_id, id DESC), "
           "ranked AS (SELECT l.player_id, l.display_name, l.progress, "
           "  (d.player_id IS NOT NULL) AS completed, a.good_evil, a.law_chaos, "
           "  ROW_NUMBER() OVER (ORDER BY l.progress DESC, l.display_name) AS rank "
           "  FROM leaderboard l LEFT JOIN done d ON d.player_id = l.player_id "
           "  LEFT JOIN align a ON a.player_id = l.player_id "
           "  WHERE l.game_id=$1) ")

    def _align(r):
        # No alignment events yet → treated as the origin (true neutral).
        return engine.alignment_short(r["good_evil"] or 0.0, r["law_chaos"] or 0.0)

    pool = await db.get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(cte + "SELECT count(*) FROM ranked", gid)
        me = await conn.fetchrow(
            cte + "SELECT rank, display_name, progress, completed, good_evil, law_chaos "
            "FROM ranked WHERE player_id=$2", gid, pid)
        if around > 0 and me:
            offset = max(0, me["rank"] - around - 1)
            limit = min(2 * around + 1, 100)
            q = ""
        if q.strip():
            rows = await conn.fetch(
                cte + "SELECT player_id, rank, display_name, progress, completed, good_evil, law_chaos FROM ranked "
                "WHERE display_name ILIKE '%'||$2||'%' ORDER BY rank LIMIT $3", gid, q.strip(), limit)
        else:
            rows = await conn.fetch(
                cte + "SELECT player_id, rank, display_name, progress, completed, good_evil, law_chaos FROM ranked "
                "ORDER BY rank OFFSET $2 LIMIT $3", gid, offset, limit)
    return {
        "total": total,
        "me": ({"rank": me["rank"], "display_name": me["display_name"], "progress": me["progress"],
                "completed": me["completed"], "alignment": _align(me)} if me else None),
        "rows": [{"rank": r["rank"], "display_name": r["display_name"], "progress": r["progress"],
                  "completed": r["completed"], "alignment": _align(r),
                  "is_me": r["player_id"] == pid} for r in rows],
    }
