"""The forced onboarding manual + comprehension quiz (GAME_DESIGN.md §6).

A player must read the manual and pass the quiz before the game starts. The quiz
is low-stakes (retryable) — its job is to make sure the player grasps the basics
that the design depends on: the log IS your progress, cheating death is a real cost,
and you advance by talking to characters and solving woven puzzles.

The manual/quiz text is dataset content: it lives in the active game's `game:` block
(`onboarding:`), loaded via gameconfig, so a different dataset can supply its own.
The engine keeps a minimal generic fallback in gameconfig.DEFAULTS. Onboarding runs
before a game is picked, so it uses the currently-active game's config.
"""
from . import gameconfig


def _onboarding(cfg: dict | None = None) -> dict:
    cfg = cfg or gameconfig.active()
    return cfg.get("onboarding", {}) or {}


def manual(cfg: dict | None = None) -> str:
    return _onboarding(cfg).get("manual", "")


def quiz(cfg: dict | None = None) -> list[dict]:
    return _onboarding(cfg).get("quiz", []) or []


def pass_threshold(cfg: dict | None = None) -> int:
    """How many correct answers are required. `None` in config → all of them."""
    pt = _onboarding(cfg).get("pass_threshold")
    return len(quiz(cfg)) if pt is None else int(pt)


def public_questions(cfg: dict | None = None) -> list[dict]:
    """Quiz without the answers, for sending to the client."""
    return [{"id": q["id"], "prompt": q["prompt"], "options": q["options"]}
            for q in quiz(cfg)]


def grade(answers: dict, cfg: dict | None = None) -> tuple[bool, int, int]:
    """answers: {question_id: chosen_index}. Returns (passed, score, total)."""
    qs = quiz(cfg)
    score = 0
    for q in qs:
        chosen = answers.get(q["id"])
        if isinstance(chosen, int) and chosen == q["answer"]:
            score += 1
    return score >= pass_threshold(cfg), score, len(qs)
