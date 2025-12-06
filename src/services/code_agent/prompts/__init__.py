"""Prompts for the coding agent."""

from .system import SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT
from .planning import PLANNING_PROMPT, PLAN_REVIEW_PROMPT
from .coding import CODING_PROMPT, FIX_ERROR_PROMPT

__all__ = [
    "SYSTEM_PROMPT",
    "REVIEWER_SYSTEM_PROMPT",
    "PLANNING_PROMPT",
    "PLAN_REVIEW_PROMPT",
    "CODING_PROMPT",
    "FIX_ERROR_PROMPT",
]
