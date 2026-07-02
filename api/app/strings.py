"""Centralized game-agnostic, user-facing message copy for the API.

These are engine UX strings (auth, session, onboarding, lobby, navigation) — not
dataset content (that lives in games/<Game>/ and is served from there). Gathered here
so the wording lives in one place. Admin/YAML-editor diagnostics and dynamic messages
(f-strings, str(exception)) stay inline at their call sites — they're developer-facing,
not player copy.
"""


class M:
    # --- session / auth guard ---
    NOT_SIGNED_IN = "You're not signed in. Please log in to continue."
    SESSION_INVALID = "Your session is invalid. Please log in again."
    SESSION_EXPIRED = "Your session has expired. Please log in again."
    ONBOARD_FIRST = "Please finish onboarding before you start playing."
    PICK_GAME = "Pick a game from the lobby first."
    ADMIN_ONLY = "This area is for administrators only."
    NO_ENTRY_NODE = "This game has no entry node."

    # --- register ---
    REGISTER_RATE = "Too many sign-up attempts from your network. Please try again later."
    PASSWORD_TOO_SHORT = "Your password must be at least 8 characters long."
    CHARACTER_NAME_REQUIRED = "Please enter a character name."
    EMAIL_TAKEN = "That email is already registered. Try logging in instead."
    CHARACTER_NAME_TAKEN = "That character name is already taken. Please choose another."

    # --- 2FA enable ---
    TOTP_RATE = "Too many attempts. Please try again later."
    BAD_AUTH_CODE = "That authenticator code isn't right. Please try again."

    # --- login ---
    BAD_CREDENTIALS = "Incorrect email or password."
    LOGIN_RATE = "Too many requests. Please slow down and try again."
    LOGIN_LOCKED = "Too many failed login attempts. Please wait 5 minutes and try again."
    TWOFA_INCOMPLETE = "Your two-factor setup isn't finished yet. Please register again to complete it."
    BAD_AUTH_OR_RECOVERY = "That authenticator or recovery code isn't right. Please try again."

    # --- lobby ---
    NO_SUCH_GAME = "No such game."

    # --- navigation (edge / walk) ---
    OPTION_UNAVAILABLE = "That option isn't available from where you are right now."
    PATH_NOT_YET = "You can't take that path yet."
    CANT_WALK_THERE = "You can't walk there from where you are."
    PATH_CLOSED = "That path is no longer open."
    WAY_BLOCKED = "The way there is blocked."

    # --- crossword puzzle ---
    XWORD_WRONG = "That word doesn't fit."
    XWORD_ALREADY = "That answer is already filled in."
    XWORD_NO_ENTRY = "Pick a clue to answer first."

    # --- travel ---
    CANT_TRAVEL_HERE = "You can't travel from here. Find a spot that opens the map first."
    NO_DESTINATION = "There's no such place to travel to."
    DONT_KNOW_WAY = "You don't know the way there yet."
    TOO_FAR = "That's too far to travel in a single step."
