"""
Code Agent - Uses ccr CLI for coding tasks.

Architecture:
- Bot AI interprets user intent and builds context
- Creates Docker sandbox with repo cloned
- Installs ccr in sandbox
- Runs coding tasks via `ccr code "prompt"`
- Streams output back for Discord

This replaces the complex AutonomousAgent with a cleaner architecture
where ccr handles all the coding work.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, List
from datetime import datetime
from enum import Enum

from .sandbox import SandboxManager, sandbox_manager, Sandbox, CommandResult

logger = logging.getLogger(__name__)


@dataclass
class TodoItem:
    """A todo item extracted from Claude Code output."""
    content: str
    status: str = "pending"  # pending, in_progress, completed


def parse_todos_from_output(output: str) -> List[TodoItem]:
    """
    Parse todo items from Claude Code output.

    Claude Code outputs todos in various formats:
    - ⬜ Pending task
    - 🔄 In progress task
    - ✅ Completed task
    - [ ] Unchecked
    - [x] Checked
    - "- task name" in todo lists
    """
    todos = []
    seen = set()

    lines = output.split('\n')

    for line in lines:
        line = line.strip()
        if not line or len(line) < 3:
            continue

        status = "pending"
        content = None

        # Check for emoji-based todos
        if line.startswith('⬜') or line.startswith('◻'):
            content = line[1:].strip().lstrip('- ').strip()
            status = "pending"
        elif line.startswith('🔄') or line.startswith('⏳'):
            content = line[1:].strip().lstrip('- ').strip()
            status = "in_progress"
        elif line.startswith('✅') or line.startswith('✓'):
            content = line[1:].strip().lstrip('- ').strip()
            status = "completed"
        elif line.startswith('❌'):
            content = line[1:].strip().lstrip('- ').strip()
            status = "failed"
        # Check for markdown checkbox todos
        elif line.startswith('- [ ]') or line.startswith('* [ ]'):
            content = line[5:].strip()
            status = "pending"
        elif line.startswith('- [x]') or line.startswith('* [x]') or line.startswith('- [X]'):
            content = line[5:].strip()
            status = "completed"
        # Check for numbered todos like "1. [in_progress] Fix bug"
        elif re.match(r'^\d+\.\s*\[(pending|in_progress|completed)\]', line):
            match = re.match(r'^\d+\.\s*\[(pending|in_progress|completed)\]\s*(.+)', line)
            if match:
                status = match.group(1)
                content = match.group(2).strip()

        if content and len(content) > 2 and content not in seen:
            # Skip generic/noisy items
            skip_patterns = ['token', 'cost', 'session', 'api', 'model']
            if not any(skip in content.lower() for skip in skip_patterns):
                seen.add(content)
                todos.append(TodoItem(content=content[:100], status=status))

    return todos[:10]  # Limit to 10 todos

# Callback types
ProgressCallback = Callable[[str], Awaitable[None]]


class AgentStatus(Enum):
    """Current status of the Claude Code agent."""
    INITIALIZING = "initializing"
    SETTING_UP = "setting_up"
    WORKING = "working"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ClaudeCodeConfig:
    """Configuration for Claude Code agent."""
    # ccr config
    api_base_url: str = "https://gen.pollinations.ai/v1/chat/completions"
    api_key: str = ""  # Pollinations doesn't require key, but ccr needs it
    default_model: str = "claude-large"
    background_model: str = "gemini"
    web_search_model: str = "perplexity-fast"

    # Timeouts
    setup_timeout: int = 180  # 3 min for npm install
    task_timeout: int = 600   # 10 min per task

    # Non-interactive mode (prevents Claude Code from prompting)
    non_interactive: bool = True


@dataclass
class TaskProgress:
    """Progress information for a task."""
    status: AgentStatus = AgentStatus.INITIALIZING
    current_step: str = ""
    steps_completed: list = field(default_factory=list)
    steps_pending: list = field(default_factory=list)
    last_output: str = ""
    error: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.utcnow)

    def elapsed_seconds(self) -> int:
        return int((datetime.utcnow() - self.started_at).total_seconds())


@dataclass
class ClaudeCodeResult:
    """Result from Claude Code execution."""
    success: bool
    output: str
    files_changed: list = field(default_factory=list)
    commits_made: list = field(default_factory=list)
    pr_url: Optional[str] = None
    error: Optional[str] = None
    duration_seconds: int = 0
    todos: List[TodoItem] = field(default_factory=list)  # Parsed todo items


class ClaudeCodeAgent:
    """
    Agent that uses Claude Code CLI for coding tasks.

    Usage:
        agent = ClaudeCodeAgent(sandbox_manager)
        result = await agent.run_task(
            sandbox_id="abc123",
            prompt="Fix the URL encoding bug in getImageURL.js",
            on_progress=update_discord_embed
        )
    """

    def __init__(
        self,
        sandbox_mgr: Optional[SandboxManager] = None,
        config: Optional[ClaudeCodeConfig] = None,
    ):
        self.sandbox_mgr = sandbox_mgr or sandbox_manager
        self.config = config or ClaudeCodeConfig()
        self._active_tasks: dict = {}
        self._progress: dict = {}

    async def setup_sandbox(self, sandbox_id: str) -> bool:
        """
        Install Claude Code and ccr in the sandbox.

        Creates a non-root user 'coder' because --dangerously-skip-permissions
        cannot be used with root for security reasons.

        Returns True if setup successful.
        """
        sandbox = self.sandbox_mgr.sandboxes.get(sandbox_id)
        if not sandbox:
            logger.error(f"Sandbox {sandbox_id} not found")
            return False

        logger.info(f"Setting up Claude Code in sandbox {sandbox_id}")

        # Install Claude Code and ccr globally
        install_cmd = "npm install -g @anthropic-ai/claude-code @musistudio/claude-code-router 2>&1"
        result = await self.sandbox_mgr.execute(
            sandbox_id,
            install_cmd,
            timeout=self.config.setup_timeout
        )

        if result.exit_code != 0:
            logger.error(f"Failed to install Claude Code: {result.stderr}")
            return False

        logger.info(f"Claude Code installed in sandbox {sandbox_id}")

        # Create non-root user 'coder' for running Claude Code
        # --dangerously-skip-permissions cannot be used as root
        user_setup_cmd = """
useradd -m coder 2>/dev/null || true
mkdir -p /home/coder/.claude-code-router
chown -R coder:coder /home/coder
chown -R coder:coder /workspace 2>/dev/null || true
"""
        await self.sandbox_mgr.execute(sandbox_id, user_setup_cmd, timeout=10)

        # Write ccr config for coder user
        ccr_config = self._build_ccr_config()
        config_cmd = f"""
cat > /home/coder/.claude-code-router/config.json << 'EOFCONFIG'
{json.dumps(ccr_config, indent=2)}
EOFCONFIG
chown coder:coder /home/coder/.claude-code-router/config.json
"""
        result = await self.sandbox_mgr.execute(sandbox_id, config_cmd, timeout=10)

        if result.exit_code != 0:
            logger.error(f"Failed to write ccr config: {result.stderr}")
            return False

        logger.info(f"ccr config written in sandbox {sandbox_id}")

        # Fix temp file permissions for ccr reference count
        temp_fix_cmd = """
touch /tmp/claude-code-reference-count.txt
chmod 666 /tmp/claude-code-reference-count.txt
"""
        await self.sandbox_mgr.execute(sandbox_id, temp_fix_cmd, timeout=5)

        # Start ccr service as coder user
        start_cmd = "su - coder -c 'ccr start' 2>&1"
        result = await self.sandbox_mgr.execute(sandbox_id, start_cmd, timeout=30)

        # ccr start may return non-zero if already running, that's ok
        if "started" in result.stdout.lower() or "running" in result.stdout.lower() or "loaded" in result.stdout.lower():
            logger.info(f"ccr service started in sandbox {sandbox_id}")
            return True

        # Check status
        status_cmd = "su - coder -c 'ccr status' 2>&1"
        result = await self.sandbox_mgr.execute(sandbox_id, status_cmd, timeout=10)

        if result.exit_code == 0 or "running" in result.stdout.lower():
            logger.info(f"ccr service running in sandbox {sandbox_id}")
            return True

        logger.error(f"ccr service failed to start: {result.stdout} {result.stderr}")
        return False

    def _build_ccr_config(self) -> dict:
        """Build the ccr configuration dictionary."""
        return {
            "LOG": True,
            "LOG_LEVEL": "debug",
            "CLAUDE_PATH": "",
            "HOST": "127.0.0.1",
            "PORT": 3456,
            "APIKEY": "",
            "API_TIMEOUT_MS": str(self.config.task_timeout * 1000),
            "PROXY_URL": "",
            "transformers": [],
            "Providers": [
                {
                    "name": "main",
                    "api_base_url": self.config.api_base_url,
                    "api_key": self.config.api_key or "dummy",
                    "models": [
                        "openai-large",
                        "gemini-large",
                        "gemini",
                        "claude-large",
                        "perplexity-fast"
                    ],
                    "transformer": {
                        "use": ["customparams"]
                    }
                }
            ],
            "StatusLine": {
                "enabled": True,
                "currentStyle": "default",
                "default": {"modules": []},
                "powerline": {"modules": []}
            },
            "Router": {
                "default": f"main,{self.config.default_model}",
                "background": f"main,{self.config.background_model}",
                "think": "",
                "longContext": "",
                "longContextThreshold": 60000,
                "webSearch": f"main,{self.config.web_search_model}",
                "image": ""
            },
            "CUSTOM_ROUTER_PATH": ""
        }

    async def run_task(
        self,
        sandbox_id: str,
        prompt: str,
        on_progress: Optional[ProgressCallback] = None,
        timeout: Optional[int] = None,
    ) -> ClaudeCodeResult:
        """
        Run a coding task using Claude Code.

        Args:
            sandbox_id: ID of the sandbox to run in
            prompt: The task prompt for Claude Code
            on_progress: Callback for progress updates
            timeout: Override default timeout

        Returns:
            ClaudeCodeResult with output and metadata
        """
        timeout = timeout or self.config.task_timeout

        # Initialize progress tracking
        progress = TaskProgress(
            status=AgentStatus.SETTING_UP,
            current_step="Setting up Claude Code",
            steps_pending=["Setup", "Run task", "Collect results"]
        )
        self._progress[sandbox_id] = progress

        if on_progress:
            await on_progress("🔧 Setting up coding environment...")

        # Setup sandbox if needed
        setup_success = await self.setup_sandbox(sandbox_id)
        if not setup_success:
            progress.status = AgentStatus.FAILED
            progress.error = "Failed to setup Claude Code in sandbox"
            return ClaudeCodeResult(
                success=False,
                output="",
                error=progress.error,
                duration_seconds=progress.elapsed_seconds()
            )

        progress.steps_completed.append("Setup")
        progress.status = AgentStatus.WORKING
        progress.current_step = "Running Claude Code"

        if on_progress:
            await on_progress("🚀 Starting task...")

        # Run Claude Code via ccr
        # Escape the prompt for shell
        escaped_prompt = prompt.replace("'", "'\\''")

        # Use ccr code with:
        # -p: print mode (non-interactive, required for automation)
        # --dangerously-skip-permissions: auto-accept tool usage (safe in sandbox)
        # Run as 'coder' user (not root) because --dangerously-skip-permissions requires non-root
        cmd = f"su - coder -c \"cd /workspace && ccr code -p --dangerously-skip-permissions '{escaped_prompt}'\" 2>&1"

        logger.info(f"Running Claude Code in sandbox {sandbox_id}: {prompt[:100]}...")

        # Execute and stream output
        result = await self._execute_with_streaming(
            sandbox_id,
            cmd,
            timeout,
            on_progress
        )

        progress.steps_completed.append("Run task")

        # Log the raw result for debugging
        logger.info(f"ccr exit_code={result.exit_code}, stdout_len={len(result.stdout)}, stderr_len={len(result.stderr)}")
        if result.stderr:
            logger.warning(f"ccr stderr: {result.stderr[:500]}")
        if result.exit_code != 0:
            logger.warning(f"ccr failed with code {result.exit_code}, stdout tail: {result.stdout[-500:] if result.stdout else 'empty'}")

        # Parse result
        if result.exit_code != 0 and not result.stdout:
            progress.status = AgentStatus.FAILED
            progress.error = result.stderr or "Claude Code exited with error"
            logger.error(f"Task failed: {progress.error}")
            return ClaudeCodeResult(
                success=False,
                output=result.stdout,
                error=progress.error,
                duration_seconds=progress.elapsed_seconds()
            )

        # Collect metadata (files changed, commits, etc.)
        progress.current_step = "Collecting results"

        files_changed = await self._get_changed_files(sandbox_id)
        commits = await self._get_recent_commits(sandbox_id)
        pr_url = self._extract_pr_url(result.stdout)

        # Parse todos from Claude Code output
        todos = parse_todos_from_output(result.stdout)

        progress.steps_completed.append("Collect results")
        progress.status = AgentStatus.COMPLETED

        if on_progress:
            status_emoji = "✅" if result.exit_code == 0 else "⚠️"
            await on_progress(f"{status_emoji} Task completed")

        return ClaudeCodeResult(
            success=result.exit_code == 0 or bool(files_changed or commits),
            output=result.stdout,
            files_changed=files_changed,
            commits_made=commits,
            pr_url=pr_url,
            duration_seconds=progress.elapsed_seconds(),
            todos=todos,
        )

    async def _execute_with_streaming(
        self,
        sandbox_id: str,
        cmd: str,
        timeout: int,
        on_progress: Optional[ProgressCallback] = None,
    ) -> CommandResult:
        """
        Execute command and stream output to progress callback.

        For now, this runs the command and returns full output.
        TODO: Implement true streaming by reading stdout line-by-line.
        """
        # For now, just execute and return
        # True streaming would require modifying sandbox to support it
        result = await self.sandbox_mgr.execute(
            sandbox_id,
            cmd,
            timeout=timeout
        )

        # Update progress with output snippets
        if on_progress and result.stdout:
            # Extract key progress indicators from Claude Code output
            lines = result.stdout.split('\n')
            for line in lines[-10:]:  # Last 10 lines
                line = line.strip()
                if line and not line.startswith('[') and len(line) < 200:
                    # Skip noisy lines
                    if any(skip in line.lower() for skip in ['token', 'cost', 'session']):
                        continue
                    # Send progress update
                    await on_progress(f"📝 {line[:100]}")

        return result

    async def _get_changed_files(self, sandbox_id: str) -> list:
        """Get list of files changed in the sandbox."""
        result = await self.sandbox_mgr.execute(
            sandbox_id,
            "git diff --name-only HEAD~1 2>/dev/null || git diff --name-only",
            timeout=10
        )

        if result.exit_code == 0 and result.stdout:
            return [f.strip() for f in result.stdout.split('\n') if f.strip()]
        return []

    async def _get_recent_commits(self, sandbox_id: str) -> list:
        """Get recent commit messages made by Claude Code."""
        result = await self.sandbox_mgr.execute(
            sandbox_id,
            "git log --oneline -5 2>/dev/null | head -5",
            timeout=10
        )

        if result.exit_code == 0 and result.stdout:
            commits = []
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line and 'claude' in line.lower():
                    commits.append(line)
            return commits
        return []

    def _extract_pr_url(self, output: str) -> Optional[str]:
        """Extract PR URL from Claude Code output if one was created."""
        # Look for GitHub PR URLs
        pr_patterns = [
            r'https://github\.com/[^/]+/[^/]+/pull/\d+',
            r'Pull request created: (https://[^\s]+)',
            r'PR: (https://[^\s]+)',
        ]

        for pattern in pr_patterns:
            match = re.search(pattern, output)
            if match:
                return match.group(1) if match.lastindex else match.group(0)

        return None

    async def pause_task(self, sandbox_id: str) -> bool:
        """
        Pause a running task.

        Note: Claude Code doesn't support pause natively.
        This cancels the current task - it can be resumed with a new prompt.
        """
        if sandbox_id in self._active_tasks:
            task = self._active_tasks[sandbox_id]
            task.cancel()

            progress = self._progress.get(sandbox_id)
            if progress:
                progress.status = AgentStatus.PAUSED

            return True
        return False

    async def send_input(self, sandbox_id: str, input_text: str) -> bool:
        """
        Send additional input to a running Claude Code session.

        Note: With NON_INTERACTIVE_MODE, this starts a new prompt
        in the same sandbox context.
        """
        # For now, just run a new ccr code command
        # The context is maintained in the sandbox (files, git state)
        progress = self._progress.get(sandbox_id)
        if progress:
            progress.current_step = f"Processing: {input_text[:50]}..."

        escaped = input_text.replace("'", "'\\''")
        cmd = f"su - coder -c \"cd /workspace && ccr code -p --dangerously-skip-permissions '{escaped}'\" 2>&1"

        result = await self.sandbox_mgr.execute(
            sandbox_id,
            cmd,
            timeout=self.config.task_timeout
        )

        return result.exit_code == 0

    def get_progress(self, sandbox_id: str) -> Optional[TaskProgress]:
        """Get current progress for a task."""
        return self._progress.get(sandbox_id)

    async def cleanup(self, sandbox_id: str):
        """Clean up resources for a task."""
        # Stop ccr service
        await self.sandbox_mgr.execute(
            sandbox_id,
            "ccr stop 2>/dev/null || true",
            timeout=10
        )

        # Remove from tracking
        self._active_tasks.pop(sandbox_id, None)
        self._progress.pop(sandbox_id, None)


# Global instance
claude_code_agent: Optional[ClaudeCodeAgent] = None


def get_claude_code_agent() -> ClaudeCodeAgent:
    """Get or create the global Claude Code agent instance."""
    global claude_code_agent
    if claude_code_agent is None:
        claude_code_agent = ClaudeCodeAgent()
    return claude_code_agent
