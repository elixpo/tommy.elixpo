"""
Base classes for agent modes.

Inspired by Roo-Code's mode system - each mode has specialized
workflows, tool permissions, and step-by-step instructions.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any, Callable, Awaitable
from enum import Enum

logger = logging.getLogger(__name__)


class ToolGroup(Enum):
    """Tool permission groups (like Roo-Code)."""
    READ = "read"           # read_file, list_files, search
    EDIT = "edit"           # write_file, apply_diff
    COMMAND = "command"     # execute shell commands
    BROWSER = "browser"     # web browsing
    GITHUB = "github"       # GitHub API operations
    SEARCH = "search"       # web search (gemini-search, perplexity)


@dataclass
class WorkflowStep:
    """A single step in the mode workflow."""
    number: int
    name: str
    instructions: str
    tools_required: list[ToolGroup] = field(default_factory=list)
    requires_approval: bool = False
    on_failure: str = "abort"  # "abort", "retry", "skip", "ask"


@dataclass
class ModeConfig:
    """Configuration for an agent mode."""
    slug: str
    name: str
    emoji: str
    role_definition: str
    when_to_use: str
    description: str
    tool_groups: list[ToolGroup]
    workflow_steps: list[WorkflowStep]
    best_practices: list[str] = field(default_factory=list)
    custom_instructions: str = ""


# Callback types for interactive mode
ProgressCallback = Callable[[str, str, Optional[str]], Awaitable[None]]
ApprovalCallback = Callable[[str, str], Awaitable[tuple[str, str]]]


@dataclass
class ModeState:
    """Current state of mode execution."""
    current_step: int = 0
    total_steps: int = 0
    step_outputs: dict[int, Any] = field(default_factory=dict)
    todos: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    completed: bool = False
    success: bool = False

    def add_todo(self, content: str, status: str = "pending"):
        """Add a todo item."""
        self.todos.append({"content": content, "status": status})

    def complete_todo(self, index: int):
        """Mark a todo as completed."""
        if 0 <= index < len(self.todos):
            self.todos[index]["status"] = "completed"

    def set_todo_in_progress(self, index: int):
        """Mark a todo as in progress."""
        if 0 <= index < len(self.todos):
            self.todos[index]["status"] = "in_progress"


class AgentMode(ABC):
    """
    Base class for agent modes.

    Each mode defines a specialized workflow for a specific task type.
    Inspired by Roo-Code's mode system with specialized agents.
    """

    @property
    @abstractmethod
    def config(self) -> ModeConfig:
        """Return the mode configuration."""
        pass

    @abstractmethod
    async def execute(
        self,
        context: dict[str, Any],
        sandbox: Any,
        model_router: Any,
        on_progress: Optional[ProgressCallback] = None,
        on_approval: Optional[ApprovalCallback] = None,
    ) -> dict[str, Any]:
        """
        Execute the mode workflow.

        Args:
            context: Input context (issue URL, PR number, task description, etc.)
            sandbox: Sandbox for code execution
            model_router: Model router for AI calls
            on_progress: Callback for progress updates
            on_approval: Callback for approval requests

        Returns:
            Result dict with success status and outputs
        """
        pass

    def get_system_prompt(self) -> str:
        """Generate the system prompt for this mode."""
        config = self.config

        prompt_parts = [
            f"# {config.emoji} {config.name}",
            "",
            f"## Role",
            config.role_definition,
            "",
            f"## Current Task",
            config.description,
            "",
        ]

        if config.best_practices:
            prompt_parts.extend([
                "## Best Practices",
                *[f"- {bp}" for bp in config.best_practices],
                "",
            ])

        if config.custom_instructions:
            prompt_parts.extend([
                "## Custom Instructions",
                config.custom_instructions,
                "",
            ])

        return "\n".join(prompt_parts)

    async def _report_progress(
        self,
        on_progress: Optional[ProgressCallback],
        phase: str,
        message: str,
        detail: Optional[str] = None
    ):
        """Report progress to callback if available."""
        if on_progress:
            try:
                await on_progress(phase, message, detail)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")

    async def _request_approval(
        self,
        on_approval: Optional[ApprovalCallback],
        phase: str,
        content: str,
        default_approve: bool = True
    ) -> tuple[str, str]:
        """
        Request approval from callback.

        Returns:
            (decision, feedback) where decision is "approve", "reject", or "modify"
        """
        if on_approval:
            try:
                return await on_approval(phase, content)
            except Exception as e:
                logger.warning(f"Approval callback failed: {e}")

        # Default behavior when no callback
        return ("approve", "") if default_approve else ("reject", "No approval callback")

    def _validate_context(self, context: dict, required_keys: list[str]) -> Optional[str]:
        """Validate that required context keys are present."""
        missing = [k for k in required_keys if k not in context or not context[k]]
        if missing:
            return f"Missing required context: {', '.join(missing)}"
        return None
