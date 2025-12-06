"""
Code Agent Modes - Roo-Code style specialized agent modes.

Each mode defines:
- role_definition: Agent's expertise and personality
- tool_groups: What tools the agent can use
- workflow: Step-by-step instructions for the mode
- when_to_use: When this mode should be activated

Architecture:
- Orchestrator: Central brain that delegates to specialized modes
- Specialized Modes: Isolated, focused agents that report back to orchestrator

Available Modes:
- orchestrator: Central coordinator that delegates tasks
- code-reviewer: Reviews code for quality, bugs, security
- bug-fixer: Fixes bugs from any source
- feature-builder: Implements new features
- test-writer: Generates tests for code
- refactorer: Code refactoring and cleanup
- doc-writer: Documentation generation
- researcher: Web search and information gathering
- investigator: Investigate issues without code changes
- issue-fixer: Fix GitHub issues autonomously
- pr-fixer: Fix PR review feedback, failing tests, merge conflicts
"""

from .base import AgentMode, ModeConfig, WorkflowStep, ToolGroup, ModeState

# Specialized modes
from .orchestrator import Orchestrator, get_mode_capabilities, list_mode_capabilities
from .code_reviewer import CodeReviewer
from .bug_fixer import BugFixer
from .feature_builder import FeatureBuilder
from .test_writer import TestWriter
from .refactorer import Refactorer
from .doc_writer import DocWriter
from .researcher import Researcher
from .investigator import Investigator
from .issue_fixer import IssueFixer
from .pr_fixer import PRFixer

# Runner
from .runner import ModeRunner, ModeRunResult, mode_runner, init_mode_runner


# Mode registry - all available modes
MODES = {
    # Orchestrator (central coordinator)
    "orchestrator": Orchestrator,

    # General purpose specialized modes
    "code-reviewer": CodeReviewer,
    "bug-fixer": BugFixer,
    "feature-builder": FeatureBuilder,
    "test-writer": TestWriter,
    "refactorer": Refactorer,
    "doc-writer": DocWriter,
    "researcher": Researcher,

    # Task-specific modes (can also be used via orchestrator)
    "investigator": Investigator,
    "issue-fixer": IssueFixer,
    "pr-fixer": PRFixer,
}


def get_mode(mode_name: str) -> AgentMode:
    """Get a mode instance by name."""
    mode_class = MODES.get(mode_name)
    if not mode_class:
        raise ValueError(f"Unknown mode: {mode_name}. Available: {list(MODES.keys())}")
    return mode_class()


def list_modes() -> list[dict]:
    """List all available modes with their metadata."""
    result = []
    for name, mode_class in MODES.items():
        mode = mode_class()
        config = mode.config
        result.append({
            "slug": config.slug,
            "name": f"{config.emoji} {config.name}",
            "description": config.description,
            "when_to_use": config.when_to_use,
        })
    return result


def get_mode_by_task(task_description: str) -> str:
    """
    Suggest a mode based on task description.

    Returns the mode slug that best matches the task.
    This is a simple heuristic - for complex routing, use the Orchestrator.
    """
    task_lower = task_description.lower()

    # Check for specific keywords
    if any(w in task_lower for w in ["review", "check", "audit", "quality"]):
        return "code-reviewer"

    if any(w in task_lower for w in ["bug", "fix", "error", "crash", "broken"]):
        return "bug-fixer"

    if any(w in task_lower for w in ["feature", "implement", "build", "create", "add"]):
        return "feature-builder"

    if any(w in task_lower for w in ["test", "coverage", "unittest", "pytest"]):
        return "test-writer"

    if any(w in task_lower for w in ["refactor", "cleanup", "clean up", "restructure"]):
        return "refactorer"

    if any(w in task_lower for w in ["doc", "document", "readme", "comment"]):
        return "doc-writer"

    if any(w in task_lower for w in ["search", "research", "find", "look up", "google"]):
        return "researcher"

    if any(w in task_lower for w in ["investigate", "analyze", "understand", "why"]):
        return "investigator"

    if any(w in task_lower for w in ["issue", "github issue"]):
        return "issue-fixer"

    if any(w in task_lower for w in ["pr", "pull request", "merge"]):
        return "pr-fixer"

    # Default to orchestrator for complex/unclear tasks
    return "orchestrator"


__all__ = [
    # Base classes
    "AgentMode",
    "ModeConfig",
    "WorkflowStep",
    "ToolGroup",
    "ModeState",

    # Orchestrator
    "Orchestrator",
    "get_mode_capabilities",
    "list_mode_capabilities",

    # Specialized modes
    "CodeReviewer",
    "BugFixer",
    "FeatureBuilder",
    "TestWriter",
    "Refactorer",
    "DocWriter",
    "Researcher",
    "Investigator",
    "IssueFixer",
    "PRFixer",

    # Registry
    "MODES",
    "get_mode",
    "list_modes",
    "get_mode_by_task",

    # Runner
    "ModeRunner",
    "ModeRunResult",
    "mode_runner",
    "init_mode_runner",
]
