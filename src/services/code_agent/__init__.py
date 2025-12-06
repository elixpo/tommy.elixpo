"""
Code Agent - Autonomous coding agent for Polli Discord bot.

Architecture:
- Gemini 2.5 Pro (gemini-large): Planning & codebase understanding (1M context)
- Claude Opus 4.5 (claude-large): Coding & implementation
- Claude Sonnet 4.5 (claude): Testing & quick iterations
- Kimi K2 Thinking (kimi-k2-thinking): Autonomous reviewer OR
- Human-in-the-loop: Real-time Discord feedback via reply messages
- Perplexity Sonar (perplexity-fast): Web search (default)
- Perplexity Reasoning (perplexity-reasoning): Complex web search

Flow:
1. UNDERSTAND (Gemini) - Analyze codebase, generate repo map
2. PLAN (Gemini) - Create implementation plan
3. REVIEW PLAN (Human or Kimi K2) - Approval/feedback with Discord replies
4. CODE (Claude Large) - Execute implementation
5. TEST (Claude) - Run tests, capture results
6. FIX LOOP (Claude) - Fix failures, retry
7. REVIEW CODE (Human or Kimi K2) - Final review with Discord replies
8. COMMIT/PR - Create branch, commit, PR

Mode System (Roo-Code style):
- Orchestrator: Central brain that delegates to specialized modes
- Specialized Modes: Isolated agents that report back to orchestrator

Available Modes:
- orchestrator: Analyzes tasks and delegates to appropriate modes
- code-reviewer: Reviews code for quality, bugs, security
- bug-fixer: Fixes bugs from any source
- feature-builder: Implements new features
- test-writer: Generates tests for code
- refactorer: Code refactoring and cleanup
- doc-writer: Documentation generation
- researcher: Web search and information gathering
- investigator: Investigate issues and propose solutions
- issue-fixer: Fix GitHub issues autonomously
- pr-fixer: Fix PR review feedback, failing tests, merge conflicts

Interactive Mode (default):
- Live progress updates sent to Discord
- Users can reply to any agent message
- Reply "approve" to continue, "reject" to cancel
- Any other reply is treated as feedback for revision
"""

from .agent import CodeAgent, code_agent
from .models import ModelRouter, model_router
from .discord_progress import (
    DiscordProgressReporter,
    HumanFeedback,
    HumanFeedbackType,
    NotificationMode,
    register_reporter,
    unregister_reporter,
    route_reply,
)
# Modes system
from .modes import (
    # Base classes
    AgentMode,
    ModeConfig,
    WorkflowStep,
    ToolGroup,
    ModeState,
    # Orchestrator
    Orchestrator,
    get_mode_capabilities,
    list_mode_capabilities,
    # Specialized modes
    CodeReviewer,
    BugFixer,
    FeatureBuilder,
    TestWriter,
    Refactorer,
    DocWriter,
    Researcher,
    Investigator,
    IssueFixer,
    PRFixer,
    # Registry
    MODES,
    get_mode,
    list_modes,
    get_mode_by_task,
    # Runner
    ModeRunner,
    ModeRunResult,
    mode_runner,
    init_mode_runner,
)

__all__ = [
    # Core agent
    "CodeAgent",
    "code_agent",
    "ModelRouter",
    "model_router",
    # Discord integration
    "DiscordProgressReporter",
    "HumanFeedback",
    "HumanFeedbackType",
    "NotificationMode",
    "register_reporter",
    "unregister_reporter",
    "route_reply",
    # Modes base classes
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
