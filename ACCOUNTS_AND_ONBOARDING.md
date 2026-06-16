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

## Security notes / deferred

This is prototype-grade and good enough to be honest about:
- No email verification, password reset, rate limiting, or account lockout yet.
- TOTP secret is stored plaintext in the DB (encrypt at rest for production).
- Sessions are opaque UUID bearer tokens with no expiry/refresh; add TTL + refresh
  and httpOnly cookies + CSRF for a real deployment (see TECH_STACK.md §Auth).
- No recovery codes for a lost authenticator.
