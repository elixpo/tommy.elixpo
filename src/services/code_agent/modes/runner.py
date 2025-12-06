"""
Mode Runner - Executes agent modes autonomously.

This integrates the mode system with the existing CodeAgent infrastructure,
allowing modes to be run from Discord commands.
"""

import logging
from typing import Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime

from .base import AgentMode, ProgressCallback, ApprovalCallback

logger = logging.getLogger(__name__)


def _get_mode(mode_name: str) -> AgentMode:
    """Get a mode instance by name (lazy import to avoid circular imports)."""
    from .orchestrator import Orchestrator
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

    modes = {
        "orchestrator": Orchestrator,
        "code-reviewer": CodeReviewer,
        "bug-fixer": BugFixer,
        "feature-builder": FeatureBuilder,
        "test-writer": TestWriter,
        "refactorer": Refactorer,
        "doc-writer": DocWriter,
        "researcher": Researcher,
        "investigator": Investigator,
        "issue-fixer": IssueFixer,
        "pr-fixer": PRFixer,
    }

    mode_class = modes.get(mode_name)
    if not mode_class:
        raise ValueError(f"Unknown mode: {mode_name}. Available: {list(modes.keys())}")
    return mode_class()


@dataclass
class ModeRunResult:
    """Result of running a mode."""
    success: bool
    mode: str
    output: dict = field(default_factory=dict)
    error: Optional[str] = None
    duration: float = 0.0
    messages: list[str] = field(default_factory=list)


class ModeRunner:
    """
    Runs agent modes autonomously.

    Integrates with:
    - ModelRouter for AI calls
    - SandboxManager for code execution
    - Discord for interactive feedback
    """

    def __init__(self, model_router: Any, sandbox_manager: Any):
        self.models = model_router
        self.sandboxes = sandbox_manager
        self._initialized = False

    async def initialize(self):
        """Initialize required components."""
        if not self._initialized:
            await self.models.initialize()
            await self.sandboxes.start()
            self._initialized = True
            logger.info("ModeRunner initialized")

    async def close(self):
        """Clean up resources."""
        await self.models.close()
        await self.sandboxes.stop()
        self._initialized = False

    async def run_mode(
        self,
        mode_name: str,
        context: dict[str, Any],
        on_progress: Optional[ProgressCallback] = None,
        on_approval: Optional[ApprovalCallback] = None,
    ) -> ModeRunResult:
        """
        Run a mode with the given context.

        Args:
            mode_name: Name of the mode to run (issue-fixer, pr-fixer, investigator)
            context: Context dict with mode-specific inputs
            on_progress: Callback for progress updates
            on_approval: Callback for approval requests

        Returns:
            ModeRunResult with output and status
        """
        await self.initialize()

        start_time = datetime.utcnow()
        messages = []

        try:
            # Get the mode
            mode = _get_mode(mode_name)
            logger.info(f"Running mode: {mode.config.name}")

            # Create sandbox for code operations
            repo = context.get("repo", "pollinations/pollinations")
            branch = context.get("branch", "main")

            sandbox = await self.sandboxes.create(
                repo_url=f"https://github.com/{repo}.git",
                branch=branch,
            )

            try:
                # Execute the mode
                result = await mode.execute(
                    context=context,
                    sandbox=sandbox,
                    model_router=self.models,
                    on_progress=on_progress,
                    on_approval=on_approval,
                )

                duration = (datetime.utcnow() - start_time).total_seconds()

                return ModeRunResult(
                    success=result.get("success", False),
                    mode=mode_name,
                    output=result,
                    error=result.get("error"),
                    duration=duration,
                    messages=messages,
                )

            finally:
                # Clean up sandbox
                await self.sandboxes.destroy(sandbox.id)

        except ValueError as e:
            # Unknown mode
            return ModeRunResult(
                success=False,
                mode=mode_name,
                error=str(e),
                duration=0.0,
            )
        except Exception as e:
            logger.exception(f"Mode execution failed: {e}")
            duration = (datetime.utcnow() - start_time).total_seconds()
            return ModeRunResult(
                success=False,
                mode=mode_name,
                error=str(e),
                duration=duration,
            )

    async def run_issue_fixer(
        self,
        issue_url: str,
        on_progress: Optional[ProgressCallback] = None,
        on_approval: Optional[ApprovalCallback] = None,
    ) -> ModeRunResult:
        """Convenience method to run issue-fixer mode."""
        return await self.run_mode(
            mode_name="issue-fixer",
            context={"issue_url": issue_url},
            on_progress=on_progress,
            on_approval=on_approval,
        )

    async def run_pr_fixer(
        self,
        pr_number: int,
        on_progress: Optional[ProgressCallback] = None,
        on_approval: Optional[ApprovalCallback] = None,
    ) -> ModeRunResult:
        """Convenience method to run pr-fixer mode."""
        return await self.run_mode(
            mode_name="pr-fixer",
            context={"pr_number": pr_number},
            on_progress=on_progress,
            on_approval=on_approval,
        )

    async def run_investigator(
        self,
        issue_url: str,
        post_to_github: bool = False,
        on_progress: Optional[ProgressCallback] = None,
        on_approval: Optional[ApprovalCallback] = None,
    ) -> ModeRunResult:
        """Convenience method to run investigator mode."""
        return await self.run_mode(
            mode_name="investigator",
            context={
                "issue_url": issue_url,
                "post_to_github": post_to_github,
            },
            on_progress=on_progress,
            on_approval=on_approval,
        )

    def list_modes(self) -> list[dict]:
        """List available modes."""
        from .orchestrator import Orchestrator
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

        modes = {
            "orchestrator": Orchestrator,
            "code-reviewer": CodeReviewer,
            "bug-fixer": BugFixer,
            "feature-builder": FeatureBuilder,
            "test-writer": TestWriter,
            "refactorer": Refactorer,
            "doc-writer": DocWriter,
            "researcher": Researcher,
            "investigator": Investigator,
            "issue-fixer": IssueFixer,
            "pr-fixer": PRFixer,
        }

        result = []
        for name, mode_class in modes.items():
            mode = mode_class()
            config = mode.config
            result.append({
                "slug": config.slug,
                "name": config.name,
                "emoji": config.emoji,
                "description": config.description,
                "when_to_use": config.when_to_use,
            })
        return result


# Global instance
mode_runner = ModeRunner(
    model_router=None,  # Set during initialization
    sandbox_manager=None,
)


async def init_mode_runner(model_router: Any, sandbox_manager: Any):
    """Initialize the mode runner with required components."""
    global mode_runner
    mode_runner = ModeRunner(
        model_router=model_router,
        sandbox_manager=sandbox_manager,
    )
    await mode_runner.initialize()
    return mode_runner
