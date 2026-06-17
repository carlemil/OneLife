# OneLife — Accounts & Onboarding

Implements real authentication and the forced onboarding from
[GAME_DESIGN.md](GAME_DESIGN.md) §6/§9, replacing the name-only stub.

---

## Flow

```
Register (email + password + display name)
   └─► account created, TOTP secret issued  → QR returned
Set up 2FA (scan QR, enter a code)
   └─► totp_enabled = true
Log in (email + password + TOTP code)
   └─► session token issued
Onboarding (read the manual, pass a 3-question quiz)
   └─► onboarded = true, game starts at Killebäckskolan
Play
```

2FA is **mandatory**: login is refused until TOTP is enabled, and the game is
refused until onboarding passes.

---

## Auth

- **Passwords** are hashed with **bcrypt** (`auth.py`); never stored or returned.
- **2FA** is **TOTP** (`pyotp`) — authenticator-app codes. On register the server
  generates a secret and returns an `otpauth://` URI plus a server-rendered
  **QR SVG** (`segno`) so the client renders it with no JS QR dependency.
- **Sessions**: one row per player (`player_sessions.player_id` is the PK). Login
  rotates the bearer `token` in place, so game state (current node, log) persists
  across logins. A 401 anywhere makes the client drop the token and return to login.

### Endpoints
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/auth/register` | email, password, display_name | → `{otpauth_uri, secret, qr_svg}` |
| POST | `/api/auth/totp/enable` | email, password, code | verifies code → enables 2FA |
| POST | `/api/auth/login` | email, password, code | → `{token, onboarded}` |
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
- **CORS + headers** — CORS restricted to `WEB_ORIGIN`; `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy` set on every response.

## Still deferred

- Email verification and password reset (need outbound email).
- Token **refresh** (today login re-issues), httpOnly cookies + CSRF (today it's a
  bearer token in localStorage).
- Rate-limit state is in-process — use Redis for multi-instance.
- User-enumeration: registration still distinguishes "email taken".
