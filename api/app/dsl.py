"""The shared condition DSL (STORY_AND_PUZZLES.md §4), evaluated in plain code.

A PlayerContext is a snapshot of the player's runtime state. Conditions are a
small JSON boolean tree; they NEVER touch an LLM.
"""
from dataclasses import dataclass, field


@dataclass
class PlayerContext:
    flags: set[str] = field(default_factory=set)
    visited_nodes: set[str] = field(default_factory=set)
    passed_gates: set[str] = field(default_factory=set)
    solved_puzzles: set[str] = field(default_factory=set)
    found_clues: set[str] = field(default_factory=set)
    known_names: set[str] = field(default_factory=set)  # character ids whose name the player has learned
    story_time: int = 0
    good_evil: float = 0.0   # alignment axis: +1 good … -1 evil
    law_chaos: float = 0.0   # alignment axis: +1 lawful … -1 chaotic


def evaluate(cond: dict | None, ctx: PlayerContext) -> bool:
    """Evaluate a condition tree. Empty / missing condition is always true."""
    if not cond:
        return True
    if "all" in cond:
        return all(evaluate(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, ctx) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], ctx)
    if "flag_set" in cond:
        return cond["flag_set"] in ctx.flags
    if "node_visited" in cond:
        return cond["node_visited"] in ctx.visited_nodes
    if "gate_passed" in cond:
        return cond["gate_passed"] in ctx.passed_gates
    if "puzzle_solved" in cond:
        return cond["puzzle_solved"] in ctx.solved_puzzles
    if "name_known" in cond:
        return cond["name_known"] in ctx.known_names
    if "clue_found" in cond:
        return cond["clue_found"] in ctx.found_clues
    if "story_time_gte" in cond:
        return ctx.story_time >= int(cond["story_time_gte"])
    # Alignment thresholds (running coordinates in [-1,1]); good/evil and
    # lawful/chaotic are the two ends of each axis.
    if "good_at_least" in cond:
        return ctx.good_evil >= float(cond["good_at_least"])
    if "evil_at_least" in cond:
        return ctx.good_evil <= -float(cond["evil_at_least"])
    if "lawful_at_least" in cond:
        return ctx.law_chaos >= float(cond["lawful_at_least"])
    if "chaotic_at_least" in cond:
        return ctx.law_chaos <= -float(cond["chaotic_at_least"])
    # Unknown predicate => fail closed (never silently unlock content).
    return False
