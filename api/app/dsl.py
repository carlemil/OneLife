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
    story_time: int = 0


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
    if "clue_found" in cond:
        return cond["clue_found"] in ctx.found_clues
    if "story_time_gte" in cond:
        return ctx.story_time >= int(cond["story_time_gte"])
    # Unknown predicate => fail closed (never silently unlock content).
    return False
