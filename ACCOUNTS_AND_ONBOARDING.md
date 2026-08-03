# OneLife — Accounts & Onboarding

Implements real authentication and the forced onboarding from
[GAME_DESIGN.md](GAME_DESIGN.md) §6/§9, replacing the name-only stub.

---

## Flow

```
Register (email + password + display name, 2FA opt-in checkbox)
   └─► account created; with 2FA: TOTP secret issued → QR returned
Set up 2FA (scan QR, enter a code)      ← optional; skippable
   └─► totp_enabled = true
Log in (email + password + TOTP code if 2FA is enabled)
   └─► session token issued
Onboarding (read the manual, pass a 3-question quiz)
   └─► onboarded = true, game starts at Killebäckskolan
Play
```

2FA is **optional** for players (on by default at registration, opt out with the
checkbox) and **required for admins** (`/api/admin/*` needs `totp_enabled`; enrol
with `python -m app.enroll_2fa`). The game is refused until onboarding passes.

**A code is only ever demanded once 2FA is confirmed** (`totp_enabled`). An account
with no secret, or with a secret that was issued but never confirmed — registration
abandoned at the QR step, or a pending `enroll_2fa --begin` — logs in with its
password alone and may leave `code` empty. A half-finished setup never locks anyone
out. Correspondingly, registration **never** reclaims an existing handle: a taken
login name or character name is always a 409.

---

## Auth

- **Passwords** are hashed with **bcrypt** (`auth.py`); never stored or returned.
- **2FA** is **TOTP** (`pyotp`) — authenticator-app codes. On register the server
  generates a secret and returns an `otpauth://` URI plus a server-rendered
  **QR SVG** (`segno`) so the client renders it with no JS QR dependency.
- **Sessions**: one row per player (`player_sessions.player_id` is the PK). Login
  rotates the bearer token in place, so game state (current node, log) persists
  across logins. A 401 anywhere makes the client drop the token and return to login.
  Only the **SHA-256 of the token** is stored (`token_hash`) — the token itself
  exists in the clear exactly once, in the login response — so a database dump
  hands over no live session. Plain SHA-256, no KDF: the token is a random UUID,
  so there is nothing to brute-force.

### Endpoints
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/auth/register` | email, password, display_name, two_factor | → `{two_factor, login_name}` (+ `{otpauth_uri, secret, qr_svg}` when `two_factor`) |
| POST | `/api/auth/totp/enable` | email, password, code | verifies code → enables 2FA |
| POST | `/api/auth/login` | email, password, code | → `{token, onboarded}`; `code` may be empty unless 2FA is enabled |
| GET | `/api/onboarding` | — (auth) | → `{manual, questions}` (no answers) |
| POST | `/api/onboarding/submit` | answers `{qid: index}` | grades; on pass starts the game |

Game endpoints (`/api/state`, `/edge`, `/gate/message`, `/puzzle/submit`,
`/rollback`, `/log`) return **403** until `onboarded` is true.

---

## Onboarding

`onboarding.py` holds the **manual** and a **3-question quiz** (the log is your
progress; rollback costs leaderboard position; you advance by talking). It's
low-stakes and **retryable** — you must get all three right, then the game is
created and you wake at the entry node. Authored as plain Python for now (not in
the YAML content pipeline, since it isn't story-graph content).

---

## Hardening (implemented)

- **Encryption at rest** — the TOTP secret is encrypted with Fernet
  (`security.py`), keyed by `ONELIFE_SECRET_KEY` (a dev key + warning if unset).
- **Rate limiting & lockout** — in-memory sliding windows on register/2FA/login;
  login locks an email after 5 failed attempts for 5 minutes (429).
- **Session TTL** — bearer tokens carry `expires_at` (7 days); expired tokens are
  rejected (401), and the client falls back to login.
- **Recovery codes** — 8 one-time backup codes issued at 2FA setup (shown once,
  stored bcrypt-hashed). Login accepts a recovery code in place of a TOTP code;
  each is single-use.
- **Single-use TOTP codes** — pyotp accepts a code for its own 30s step ±1, so a
  code someone observes stays valid for ~90s after its owner used it. The spent
  step is recorded (`players.totp_last_step`) and a code is refused the second
  time (`main._consume_totp`), via a compare-and-set so two racing logins can't
  both claim it.
- **CORS + headers** — CORS restricted to `WEB_ORIGIN`; `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy` set on every response. The deployed site
  adds HSTS and a CSP at the Caddy layer, which also covers the web app's HTML
  (the API middleware only sees `/api`).
- **Production posture** — with `ONELIFE_ENV=prod` (set by the prod overlay) the
  FastAPI auto-docs are off and an unset `ONELIFE_SECRET_KEY` is fatal rather than
  a warning.

## Still deferred

- Email verification and password reset (need outbound email).
- Token **refresh** (today login re-issues), httpOnly cookies + CSRF (today it's a
  bearer token in localStorage).
- Rate-limit state is in-process — use Redis for multi-instance. (And on a Docker
  Desktop for Windows host the source address is NAT'd, so per-IP limits collapse
  into one bucket regardless.)
- User-enumeration: registration still distinguishes "email taken" from "character
  name taken". Kept deliberately — the login name *is* the handle, and a player who
  can't tell which of the two fields collided can't finish registering.
