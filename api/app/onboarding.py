"""The forced onboarding manual + comprehension quiz (GAME_DESIGN.md §6).

A player must read the manual and pass the quiz before the game starts. The quiz
is low-stakes (retryable) — its job is to make sure the player grasps the basics
that the design depends on: the log IS your progress, cheating death is a real cost,
and you advance by talking to characters and solving woven puzzles.
"""

MANUAL = """\
WELCOME TO ONELIFE

You wake somewhere you shouldn't be. The world is dark and genuinely dangerous — wrong moves can get you killed.

📝 KEEP A PEN AND PAPER HANDY — you'll want to note names, numbers, and stray details. This mystery is meant to be pieced together by hand.

HOW IT WORKS
• The game is a stream of text. You act by choosing what to do, talking to the people (and places) you meet, and solving the puzzles woven into them.
• The game keeps a LOG of everything that happens. Your log IS your progress.
• Some characters must be TALKED THROUGH — say the right kind of thing and they open up. There's no single magic phrase; be human about it.
• Puzzles are hidden in the world. Pay attention to what people say and what you read — the clues are there.

DEATH & CHEATING DEATH
• You can die, and you can get stuck. When that happens you can CHEAT DEATH: rewind your LOG to an earlier point and carry on from there.
• Cheating death is never free: it erases the progress you made after that point and costs you positions on the LEADERBOARD. Use it, but spend it wisely.

THE LEADERBOARD
• You earn Progress for actions, discoveries, and dialogue. The leaderboard ranks players by total Progress. Other players share this world — their stories can ripple into yours.

Read this, then prove you've got the basics.
"""

# answer = index of the correct option.
QUIZ = [
    {
        "id": "q-log",
        "prompt": "What represents your progress in OneLife?",
        "options": [
            "Your inventory of items",
            "The log of everything that happens to you",
            "Your character's health bar",
            "The number of rooms you've unlocked",
        ],
        "answer": 1,
    },
    {
        "id": "q-rollback",
        "prompt": "What happens when you cheat death to escape a bad situation?",
        "options": [
            "Nothing — it's a free undo",
            "You lose progress made after that point and drop on the leaderboard",
            "You gain bonus Progress for surviving",
            "Your account is reset",
        ],
        "answer": 1,
    },
    {
        "id": "q-advance",
        "prompt": "How do you mainly get past the people you meet?",
        "options": [
            "By fighting them",
            "By paying them coins",
            "By talking to them and saying the right kind of thing",
            "By ignoring them and walking past",
        ],
        "answer": 2,
    },
]

PASS_THRESHOLD = len(QUIZ)  # must get all correct (low-stakes, retryable)


def public_questions() -> list[dict]:
    """Quiz without the answers, for sending to the client."""
    return [{"id": q["id"], "prompt": q["prompt"], "options": q["options"]} for q in QUIZ]


def grade(answers: dict) -> tuple[bool, int, int]:
    """answers: {question_id: chosen_index}. Returns (passed, score, total)."""
    score = 0
    for q in QUIZ:
        chosen = answers.get(q["id"])
        if isinstance(chosen, int) and chosen == q["answer"]:
            score += 1
    return score >= PASS_THRESHOLD, score, len(QUIZ)
