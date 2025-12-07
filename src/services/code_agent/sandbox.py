"""
Persistent Docker sandbox for ccr code execution.

Architecture:
- Single persistent container "polly_sandbox" running 24/7
- Volume mount: data/sandbox/workspace -> /workspace in container
- ccr service runs inside, handles multiple concurrent tasks
- Each task creates a git branch for isolation
- Bot AI handles all git push/PR operations (not ccr)

The sandbox survives bot restarts via Docker's restart policy.
All files are stored in data/sandbox/ for easy access/cleanup.
"""

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Project root (Polli/) - dynamic, not hardcoded
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# Sandbox data directory
SANDBOX_DIR = PROJECT_ROOT / "data" / "sandbox"
WORKSPACE_DIR = SANDBOX_DIR / "workspace"
CCR_CONFIG_DIR = SANDBOX_DIR / "ccr_config"

# Source repo for syncing (embeddings repo)
REPO_SOURCE_DIR = PROJECT_ROOT / "data" / "repo" / "pollinations_pollinations"

# Container name (persistent)
CONTAINER_NAME = "polly_sandbox"


@dataclass
class SandboxConfig:
    """Configuration for the persistent sandbox."""
    image: str = "node:20"  # Full image with bash, git, build tools
    memory_limit: str = "4g"
    cpu_limit: float = 4.0
    restart_policy: str = "unless-stopped"  # Survives bot/host restarts
    network_enabled: bool = True  # For npm install, API calls


@dataclass
class CommandResult:
    """Result of a command execution."""
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration: float = 0.0


@dataclass
class TaskBranch:
    """Represents a task running on a git branch."""
    branch_name: str
    task_id: str
    user_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: str = "active"  # active, completed, abandoned
    description: str = ""


class PersistentSandbox:
    """
    Manages a single persistent Docker sandbox for all ccr tasks.

    Features:
    - Single container running 24/7
    - Volume mount for persistence (data/sandbox/workspace)
    - Branch-based task isolation
    - Concurrent task support (multiple ccr processes)
    - Survives bot restarts

    Usage:
        sandbox = PersistentSandbox()
        await sandbox.ensure_running()

        # Create branch for task
        branch = await sandbox.create_task_branch("user123", "Fix bug in API")

        # Run ccr on that branch
        result = await sandbox.run_ccr(branch, "Fix the null pointer bug")

        # After task, Bot AI handles push/PR
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self.active_branches: dict[str, TaskBranch] = {}
        self._setup_directories()

    def _setup_directories(self):
        """Create necessary directories."""
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        CCR_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"Sandbox directories ready at {SANDBOX_DIR}")

    async def ensure_running(self) -> bool:
        """
        Ensure the sandbox container is running.

        - If container exists and running: use it
        - If container exists but stopped: start it
        - If container doesn't exist: create it

        Returns True if sandbox is ready.
        """
        # Check if container exists
        check_result = await self._run_host_command([
            "docker", "inspect", CONTAINER_NAME
        ])

        if check_result.exit_code == 0:
            # Container exists, check if running
            status_result = await self._run_host_command([
                "docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME
            ])

            if "true" in status_result.stdout.lower():
                logger.info(f"Sandbox {CONTAINER_NAME} already running")
                return True
            else:
                # Start stopped container
                logger.info(f"Starting stopped sandbox {CONTAINER_NAME}")
                start_result = await self._run_host_command([
                    "docker", "start", CONTAINER_NAME
                ])
                if start_result.exit_code == 0:
                    await self._ensure_ccr_running()
                    return True
                else:
                    logger.error(f"Failed to start container: {start_result.stderr}")
                    return False
        else:
            # Container doesn't exist, create it
            return await self._create_container()

    async def _create_container(self) -> bool:
        """Create the persistent sandbox container."""
        logger.info(f"Creating persistent sandbox {CONTAINER_NAME}")

        config = self.config

        # Convert paths for Docker (handle Windows paths)
        workspace_mount = str(WORKSPACE_DIR.resolve()).replace("\\", "/")
        ccr_config_mount = str(CCR_CONFIG_DIR.resolve()).replace("\\", "/")

        cmd = [
            "docker", "run", "-d",
            "--name", CONTAINER_NAME,
            "-v", f"{workspace_mount}:/workspace",
            "-v", f"{ccr_config_mount}:/home/coder/.claude-code-router",
            "-w", "/workspace",
            "--memory", config.memory_limit,
            "--cpus", str(config.cpu_limit),
            "--restart", config.restart_policy,
        ]

        if not config.network_enabled:
            cmd.extend(["--network", "none"])

        cmd.extend([config.image, "tail", "-f", "/dev/null"])

        result = await self._run_host_command(cmd)
        if result.exit_code != 0:
            logger.error(f"Failed to create container: {result.stderr}")
            return False

        logger.info(f"Container created: {result.stdout.strip()[:12]}")

        # Initial setup
        await self._initial_setup()

        return True

    async def _initial_setup(self):
        """One-time setup when container is first created."""
        logger.info("Running initial sandbox setup...")

        # Create non-root user (ccr requires non-root for --dangerously-skip-permissions)
        await self.execute("useradd -m coder 2>/dev/null || true")

        # Install ccr
        logger.info("Installing ccr (this may take a minute)...")
        install_result = await self.execute(
            "npm install -g @anthropic-ai/claude-code @musistudio/claude-code-router 2>&1",
            timeout=300
        )
        if install_result.exit_code != 0:
            logger.error(f"Failed to install ccr: {install_result.stderr}")
        else:
            logger.info("ccr installed successfully")

        # Setup permissions
        await self.execute("chown -R coder:coder /home/coder")
        await self.execute("chown -R coder:coder /workspace 2>/dev/null || true")

        # Fix temp file permissions for ccr
        await self.execute("touch /tmp/claude-code-reference-count.txt && chmod 666 /tmp/claude-code-reference-count.txt")

        # Setup git config (for both root and coder user)
        await self.execute("git config --global user.email 'polly@pollinations.ai'")
        await self.execute("git config --global user.name 'Polly Bot'")
        await self.execute("git config --global --add safe.directory '*'")
        # Also set for coder user
        await self.execute("su - coder -c \"git config --global user.email 'polly@pollinations.ai'\"")
        await self.execute("su - coder -c \"git config --global user.name 'Polly Bot'\"")
        await self.execute("su - coder -c \"git config --global --add safe.directory '*'\"")

        # Write ccr config
        await self._write_ccr_config()

        # Start ccr service
        await self._ensure_ccr_running()

        logger.info("Initial setup complete")

    async def _write_ccr_config(self):
        """Write ccr configuration file."""
        from ...config import config as app_config

        ccr_config = {
            "LOG": True,
            "LOG_LEVEL": "info",
            "CLAUDE_PATH": "",
            "HOST": "127.0.0.1",
            "PORT": 3456,
            "APIKEY": "",
            "API_TIMEOUT_MS": "600000",  # 10 minutes
            "PROXY_URL": "",
            "transformers": [],
            "Providers": [
                {
                    "name": "main",
                    "api_base_url": "https://gen.pollinations.ai/v1/chat/completions",
                    "api_key": app_config.pollinations_token if hasattr(app_config, 'pollinations_token') else "",
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
                "default": "main,claude-large",
                "background": "main,gemini",
                "think": "",
                "longContext": "",
                "longContextThreshold": 60000,
                "webSearch": "main,perplexity-fast",
                "image": ""
            },
            "CUSTOM_ROUTER_PATH": ""
        }

        config_path = CCR_CONFIG_DIR / "config.json"
        config_path.write_text(json.dumps(ccr_config, indent=2))

        # Also copy to container's coder home (in case volume mount timing issue)
        await self.execute(f"mkdir -p /home/coder/.claude-code-router")
        await self.execute(f"chown -R coder:coder /home/coder/.claude-code-router")

        logger.info(f"ccr config written to {config_path}")

    async def _ensure_ccr_running(self):
        """Ensure ccr service is running."""
        # Start ccr as coder user
        await self.execute("su - coder -c 'ccr start' 2>&1", timeout=30)
        await asyncio.sleep(2)

        # Verify
        status = await self.execute("su - coder -c 'ccr status' 2>&1")
        if "running" in status.stdout.lower():
            logger.info("ccr service is running")
        else:
            logger.warning(f"ccr status unclear: {status.stdout}")

    async def sync_repo(self, force: bool = False) -> bool:
        """
        Sync the pollinations repo from data/repo to workspace.

        Only syncs if:
        - Workspace is empty, OR
        - force=True

        The source repo in data/repo/ is kept up-to-date by the embeddings system.
        """
        workspace_repo = WORKSPACE_DIR / "pollinations"

        if workspace_repo.exists() and not force:
            logger.info("Workspace repo already exists, skipping sync")
            return True

        if not REPO_SOURCE_DIR.exists():
            logger.error(f"Source repo not found at {REPO_SOURCE_DIR}")
            return False

        logger.info(f"Syncing repo from {REPO_SOURCE_DIR} to workspace...")

        # Clear existing workspace repo if force sync
        if workspace_repo.exists() and force:
            shutil.rmtree(workspace_repo, ignore_errors=True)

        # Copy repo to workspace
        try:
            shutil.copytree(REPO_SOURCE_DIR, workspace_repo, dirs_exist_ok=True)
            logger.info("Repo copied to workspace")
        except Exception as e:
            logger.error(f"Failed to copy repo: {e}")
            return False

        # Ensure proper permissions in container
        await self.execute("chown -R coder:coder /workspace/pollinations 2>/dev/null || true")

        # Reset to main branch
        await self.execute(
            "cd /workspace/pollinations && git checkout main 2>/dev/null || true",
            as_coder=True
        )

        return True

    async def create_task_branch(
        self,
        user_id: str,
        task_description: str,
        task_id: Optional[str] = None
    ) -> TaskBranch:
        """
        Create a new git branch for a task.

        Each task gets its own branch for isolation.
        Multiple users can work concurrently on different branches.

        IMPORTANT: Always fetches latest from origin and branches from origin/main
        to ensure we're working with the latest code.
        """
        import uuid

        task_id = task_id or str(uuid.uuid4())[:8]
        branch_name = f"task/{task_id}"

        # CRITICAL: Fetch latest from origin first
        logger.info("Fetching latest from origin...")
        fetch_result = await self.execute(
            "cd /workspace/pollinations && git fetch origin main",
            as_coder=True
        )
        if fetch_result.exit_code != 0:
            logger.warning(f"git fetch failed: {fetch_result.stderr}")

        # Ensure we're on main first and update it to origin/main
        await self.execute(
            "cd /workspace/pollinations && git checkout main 2>/dev/null || true",
            as_coder=True
        )

        # Reset local main to origin/main to ensure we're up to date
        reset_result = await self.execute(
            "cd /workspace/pollinations && git reset --hard origin/main",
            as_coder=True
        )
        if reset_result.exit_code != 0:
            logger.warning(f"git reset failed: {reset_result.stderr}")

        # Create and checkout new branch FROM the updated main
        result = await self.execute(
            f"cd /workspace/pollinations && git checkout -b {branch_name}",
            as_coder=True
        )

        if result.exit_code != 0:
            # Branch might exist, delete it and recreate from fresh main
            logger.info(f"Branch {branch_name} exists, recreating from fresh main...")
            await self.execute(
                f"cd /workspace/pollinations && git branch -D {branch_name} 2>/dev/null || true",
                as_coder=True
            )
            result = await self.execute(
                f"cd /workspace/pollinations && git checkout -b {branch_name}",
                as_coder=True
            )

        branch = TaskBranch(
            branch_name=branch_name,
            task_id=task_id,
            user_id=user_id,
            description=task_description
        )

        self.active_branches[task_id] = branch
        logger.info(f"Created task branch {branch_name} for user {user_id} (from latest origin/main)")

        return branch

    async def run_ccr(
        self,
        branch: TaskBranch,
        prompt: str,
    ) -> CommandResult:
        """
        Run ccr on a specific task branch.

        Args:
            branch: TaskBranch to work on
            prompt: The task prompt for ccr

        Returns:
            CommandResult with ccr output
        """
        # Ensure we're on the right branch
        await self.execute(
            f"cd /workspace/pollinations && git checkout {branch.branch_name}",
            as_coder=True
        )

        # Escape prompt for shell
        escaped_prompt = prompt.replace("'", "'\\''").replace('"', '\\"')

        # Run ccr
        # -p: print mode (non-interactive)
        # --dangerously-skip-permissions: auto-accept (safe in sandbox)
        cmd = f'cd /workspace/pollinations && ANTHROPIC_API_KEY=dummy ccr code -p --dangerously-skip-permissions "{escaped_prompt}"'

        logger.info(f"Running ccr on branch {branch.branch_name}: {prompt[:100]}...")

        result = await self.execute(cmd, as_coder=True, timeout=None)  # No timeout

        return result

    async def get_branch_diff(self, branch: TaskBranch) -> str:
        """Get the git diff for a task branch."""
        result = await self.execute(
            f"cd /workspace/pollinations && git diff main...{branch.branch_name}",
            as_coder=True
        )
        return result.stdout

    async def get_branch_files_changed(self, branch: TaskBranch) -> list[str]:
        """Get list of files changed on a task branch."""
        result = await self.execute(
            f"cd /workspace/pollinations && git diff --name-only main...{branch.branch_name}",
            as_coder=True
        )
        if result.exit_code == 0 and result.stdout:
            return [f.strip() for f in result.stdout.split('\n') if f.strip()]
        return []

    async def cleanup_branch(self, branch: TaskBranch):
        """Delete a task branch after completion/abandonment."""
        # Switch to main first
        await self.execute(
            "cd /workspace/pollinations && git checkout main",
            as_coder=True
        )

        # Delete the branch
        await self.execute(
            f"cd /workspace/pollinations && git branch -D {branch.branch_name}",
            as_coder=True
        )

        # Remove from tracking
        self.active_branches.pop(branch.task_id, None)

        logger.info(f"Cleaned up branch {branch.branch_name}")

    async def list_branches(self) -> list[str]:
        """List all task branches."""
        result = await self.execute(
            "cd /workspace/pollinations && git branch --list 'task/*'",
            as_coder=True
        )
        if result.exit_code == 0 and result.stdout:
            return [b.strip().lstrip('* ') for b in result.stdout.split('\n') if b.strip()]
        return []

    async def execute(
        self,
        command: str,
        timeout: Optional[int] = 300,
        as_coder: bool = False,
    ) -> CommandResult:
        """
        Execute a command in the sandbox container.

        Args:
            command: Command to run
            timeout: Timeout in seconds (None for no timeout)
            as_coder: Run as 'coder' user instead of root

        Returns:
            CommandResult with output
        """
        if as_coder:
            command = f"su - coder -c '{command}'"

        cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", command]

        return await self._run_host_command(cmd, timeout)

    async def _run_host_command(
        self,
        cmd: list[str],
        timeout: Optional[int] = 60
    ) -> CommandResult:
        """Run a command on the host system."""
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if timeout:
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    return CommandResult(
                        exit_code=-1,
                        stdout="",
                        stderr=f"Command timed out after {timeout}s",
                        timed_out=True,
                        duration=loop.time() - start_time
                    )
            else:
                # No timeout
                stdout, stderr = await proc.communicate()

            return CommandResult(
                exit_code=proc.returncode or 0,
                stdout=stdout.decode(errors="replace"),
                stderr=stderr.decode(errors="replace"),
                duration=loop.time() - start_time
            )

        except Exception as e:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=str(e),
                duration=loop.time() - start_time
            )

    async def stop(self):
        """Stop the sandbox container (for maintenance)."""
        await self._run_host_command(["docker", "stop", CONTAINER_NAME])
        logger.info(f"Stopped sandbox {CONTAINER_NAME}")

    async def destroy(self):
        """Completely remove the sandbox container and data."""
        await self._run_host_command(["docker", "stop", CONTAINER_NAME])
        await self._run_host_command(["docker", "rm", CONTAINER_NAME])

        # Optionally clean workspace (uncomment if needed)
        # if WORKSPACE_DIR.exists():
        #     shutil.rmtree(WORKSPACE_DIR, ignore_errors=True)

        logger.info(f"Destroyed sandbox {CONTAINER_NAME}")

    async def is_running(self) -> bool:
        """Check if sandbox container is running."""
        result = await self._run_host_command([
            "docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME
        ])
        return "true" in result.stdout.lower()

    def get_workspace_path(self) -> Path:
        """Get the host path to workspace (for direct file access)."""
        return WORKSPACE_DIR

    def get_repo_path(self) -> Path:
        """Get the host path to the pollinations repo in workspace."""
        return WORKSPACE_DIR / "pollinations"

    async def setup_github_credentials(self, repo: str = "pollinations/pollinations") -> bool:
        """
        Configure GitHub App credentials in the sandbox for push operations.

        Uses the GitHub App (Polly Bot) for authentication:
        - Creates a git credential helper that returns the App token
        - Tokens auto-refresh (1 hour validity)
        - All pushes show as "Polly Bot" author

        Args:
            repo: Repository in owner/repo format

        Returns:
            True if credentials configured successfully
        """
        from ..github_auth import github_app_auth

        if not github_app_auth:
            logger.warning("GitHub App auth not configured, cannot setup sandbox credentials")
            return False

        try:
            # Get a fresh token
            token = await github_app_auth.get_token()
            if not token:
                logger.error("Failed to get GitHub App token")
                return False

            # Create credential helper script that returns the token
            # This script will be called by git when it needs credentials
            credential_script = f'''#!/bin/bash
# GitHub App credential helper for Polly Bot
echo "username=x-access-token"
echo "password={token}"
'''
            # Write the credential helper script to sandbox
            # First, create the script content as a file on host, then copy to container
            helper_path = SANDBOX_DIR / "git-credential-polly"
            helper_path.write_text(credential_script)

            # Copy to container
            await self._run_host_command([
                "docker", "cp",
                str(helper_path),
                f"{CONTAINER_NAME}:/usr/local/bin/git-credential-polly"
            ])

            # Make executable
            await self.execute("chmod +x /usr/local/bin/git-credential-polly")

            # Configure git to use this credential helper
            await self.execute(
                "cd /workspace/pollinations && git config credential.helper '/usr/local/bin/git-credential-polly'",
                as_coder=True
            )

            # Set remote URL to HTTPS (not SSH)
            await self.execute(
                f"cd /workspace/pollinations && git remote set-url origin https://github.com/{repo}.git",
                as_coder=True
            )

            # Clean up local helper file
            helper_path.unlink(missing_ok=True)

            logger.info("GitHub App credentials configured in sandbox")
            return True

        except Exception as e:
            logger.error(f"Failed to setup GitHub credentials: {e}")
            return False

    async def refresh_github_token(self, repo: str = "pollinations/pollinations") -> bool:
        """
        Refresh the GitHub App token in the sandbox.

        Call this before push operations to ensure token is valid.
        Tokens expire after ~1 hour.
        """
        return await self.setup_github_credentials(repo)

    async def push_branch(self, branch_name: str, repo: str = "pollinations/pollinations") -> CommandResult:
        """
        Push a branch to GitHub using the configured App credentials.

        Args:
            branch_name: Name of the branch to push
            repo: Repository in owner/repo format

        Returns:
            CommandResult with push output
        """
        # Refresh credentials before push (ensures valid token)
        creds_ok = await self.refresh_github_token(repo)
        if not creds_ok:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr="Failed to configure GitHub credentials"
            )

        # Push the branch
        result = await self.execute(
            f"cd /workspace/pollinations && git push -u origin {branch_name}",
            as_coder=True,
            timeout=120
        )

        return result


# =============================================================================
# BACKWARD COMPATIBILITY
# =============================================================================

# Keep old classes for any code that might reference them
@dataclass
class Sandbox:
    """Legacy sandbox class - now uses PersistentSandbox internally."""
    id: str
    container_id: Optional[str] = None
    workspace_path: Path = None
    repo_url: Optional[str] = None
    branch: str = "main"
    created_at: datetime = field(default_factory=datetime.utcnow)
    config: SandboxConfig = field(default_factory=SandboxConfig)
    initiated_by: Optional[str] = None
    initiated_source: Optional[str] = None
    pending_destruction: bool = False


class SandboxManager:
    """
    Legacy SandboxManager - wraps PersistentSandbox for backward compatibility.

    New code should use PersistentSandbox directly.
    """

    def __init__(self, **kwargs):
        self._persistent = PersistentSandbox()
        self.sandboxes: dict[str, Sandbox] = {}
        self.use_docker = True

    async def start(self):
        """Start the sandbox manager."""
        await self._persistent.ensure_running()
        await self._persistent.sync_repo()
        logger.info("SandboxManager started (persistent mode)")

    async def stop(self):
        """Stop the sandbox manager."""
        # Don't stop container - it's persistent!
        logger.info("SandboxManager stopped (container still running)")

    async def create(
        self,
        repo_url: Optional[str] = None,
        branch: str = "main",
        config: Optional[SandboxConfig] = None,
        initiated_by: Optional[str] = None,
        initiated_source: Optional[str] = None,
    ) -> Sandbox:
        """Create a sandbox (actually creates a task branch in persistent sandbox)."""
        import uuid
        sandbox_id = str(uuid.uuid4())[:8]

        # Create task branch
        task_branch = await self._persistent.create_task_branch(
            user_id=initiated_by or "unknown",
            task_description=f"Task from {initiated_source or 'unknown'}",
            task_id=sandbox_id
        )

        sandbox = Sandbox(
            id=sandbox_id,
            container_id=CONTAINER_NAME,
            workspace_path=self._persistent.get_repo_path(),
            repo_url=repo_url,
            branch=task_branch.branch_name,
            config=config or SandboxConfig(),
            initiated_by=initiated_by,
            initiated_source=initiated_source,
        )

        self.sandboxes[sandbox_id] = sandbox
        return sandbox

    async def execute(
        self,
        sandbox_id: str,
        command: str,
        timeout: Optional[int] = None,
        env: Optional[dict] = None,
    ) -> CommandResult:
        """Execute a command in the sandbox."""
        sandbox = self.sandboxes.get(sandbox_id)

        # Add env vars to command if provided
        if env:
            env_str = " ".join(f"{k}={v}" for k, v in env.items())
            command = f"{env_str} {command}"

        # If we have a sandbox, work on its branch
        if sandbox and sandbox.branch != "main":
            # Ensure we're on the right branch
            await self._persistent.execute(
                f"cd /workspace/pollinations && git checkout {sandbox.branch} 2>/dev/null || true",
                as_coder=True
            )

        return await self._persistent.execute(command, timeout=timeout, as_coder=True)

    async def destroy(self, sandbox_id: str, force: bool = False):
        """Destroy a sandbox (cleans up task branch)."""
        sandbox = self.sandboxes.pop(sandbox_id, None)
        if sandbox and sandbox.branch.startswith("task/"):
            # Clean up the branch
            task_id = sandbox.branch.replace("task/", "")
            branch = self._persistent.active_branches.get(task_id)
            if branch:
                await self._persistent.cleanup_branch(branch)

    def get_workspace_path(self, sandbox_id: str) -> Optional[Path]:
        """Get workspace path for a sandbox."""
        return self._persistent.get_repo_path()

    async def read_file(self, sandbox_id: str, file_path: str) -> str:
        """Read a file from the sandbox workspace."""
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            raise FileNotFoundError(f"Sandbox {sandbox_id} not found")

        # Ensure we're on the right branch
        if sandbox.branch != "main":
            await self._persistent.execute(
                f"cd /workspace/pollinations && git checkout {sandbox.branch} 2>/dev/null || true",
                as_coder=True
            )

        # Read the file
        result = await self._persistent.execute(
            f"cat /workspace/pollinations/{file_path}",
            as_coder=True
        )

        if result.exit_code != 0:
            raise FileNotFoundError(f"File not found: {file_path}")

        return result.stdout

    async def write_file(self, sandbox_id: str, file_path: str, content: str):
        """Write a file to the sandbox workspace."""
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            raise FileNotFoundError(f"Sandbox {sandbox_id} not found")

        # Ensure we're on the right branch
        if sandbox.branch != "main":
            await self._persistent.execute(
                f"cd /workspace/pollinations && git checkout {sandbox.branch} 2>/dev/null || true",
                as_coder=True
            )

        # Write to a temp file first, then move (handles special chars)
        import base64
        encoded = base64.b64encode(content.encode()).decode()
        result = await self._persistent.execute(
            f"echo '{encoded}' | base64 -d > /workspace/pollinations/{file_path}",
            as_coder=True
        )

        if result.exit_code != 0:
            raise IOError(f"Failed to write file: {result.stderr}")


# =============================================================================
# GLOBAL INSTANCES
# =============================================================================

# The single persistent sandbox
_persistent_sandbox: Optional[PersistentSandbox] = None

def get_persistent_sandbox() -> PersistentSandbox:
    """Get or create the global persistent sandbox instance."""
    global _persistent_sandbox
    if _persistent_sandbox is None:
        _persistent_sandbox = PersistentSandbox()
    return _persistent_sandbox


# Legacy sandbox manager (wraps persistent sandbox)
_sandbox_manager: Optional[SandboxManager] = None

def get_sandbox_manager() -> SandboxManager:
    """Get or create the global sandbox manager instance."""
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = SandboxManager()
    return _sandbox_manager


# Lazy proxy for backward compatibility
class _LazySandboxManager:
    """Lazy proxy for sandbox_manager."""
    def __getattr__(self, name):
        return getattr(get_sandbox_manager(), name)

sandbox_manager = _LazySandboxManager()
