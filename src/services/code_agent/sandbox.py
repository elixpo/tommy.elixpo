"""
Docker sandbox manager for safe code execution.

Provides isolated environments for:
- Cloning repositories
- Running tests
- Executing commands
- File operations

Each sandbox is a Docker container with the repo mounted.
"""

import asyncio
import logging
import os
import uuid
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    """Configuration for a sandbox."""
    image: str = "python:3.11-slim"
    memory_limit: str = "2g"
    cpu_limit: float = 2.0
    timeout: int = 300  # 5 minutes default
    network_enabled: bool = True  # For pip install etc


@dataclass
class CommandResult:
    """Result of a command execution."""
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration: float = 0.0


@dataclass
class Sandbox:
    """A running sandbox instance."""
    id: str
    container_id: Optional[str] = None
    workspace_path: Path = None
    repo_url: Optional[str] = None
    branch: str = "main"
    created_at: datetime = field(default_factory=datetime.utcnow)
    config: SandboxConfig = field(default_factory=SandboxConfig)
    _process: Optional[asyncio.subprocess.Process] = None


class SandboxManager:
    """
    Manages Docker sandboxes for code execution.

    Can run in two modes:
    1. Docker mode (production) - Full isolation in containers
    2. Local mode (development) - Direct execution with temp directories

    Supports on-demand file copy from a local reference repo (e.g., embeddings repo)
    to avoid cloning large repos repeatedly.
    """

    def __init__(
        self,
        base_path: str = "/tmp/polli_sandboxes",
        use_docker: bool = True,
        local_repo_path: Optional[str] = None,
    ):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.use_docker = use_docker
        self.sandboxes: dict[str, Sandbox] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

        # Local repo for on-demand file copy (e.g., embeddings repo)
        self.local_repo_path: Optional[Path] = Path(local_repo_path) if local_repo_path else None
        if self.local_repo_path and not self.local_repo_path.exists():
            logger.warning(f"Local repo path does not exist: {local_repo_path}")
            self.local_repo_path = None

    async def start(self):
        """Start the sandbox manager and cleanup task."""
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        logger.info(f"SandboxManager started (docker={self.use_docker})")

    async def stop(self):
        """Stop the sandbox manager and clean up all sandboxes."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Clean up all sandboxes
        for sandbox_id in list(self.sandboxes.keys()):
            await self.destroy(sandbox_id)

        logger.info("SandboxManager stopped")

    async def create(
        self,
        repo_url: Optional[str] = None,
        branch: str = "main",
        config: Optional[SandboxConfig] = None,
    ) -> Sandbox:
        """
        Create a new sandbox.

        Args:
            repo_url: Git repository URL to clone (optional)
            branch: Branch to checkout
            config: Sandbox configuration

        Returns:
            Sandbox instance
        """
        sandbox_id = str(uuid.uuid4())[:8]
        workspace = self.base_path / sandbox_id
        workspace.mkdir(parents=True, exist_ok=True)

        sandbox = Sandbox(
            id=sandbox_id,
            workspace_path=workspace,
            repo_url=repo_url,
            branch=branch,
            config=config or SandboxConfig(),
        )

        if self.use_docker:
            await self._create_docker_sandbox(sandbox)
        else:
            await self._create_local_sandbox(sandbox)

        # Clone repo if provided
        if repo_url:
            await self._clone_repo(sandbox, repo_url, branch)

        self.sandboxes[sandbox_id] = sandbox
        logger.info(f"Created sandbox {sandbox_id} for {repo_url or 'empty'}")

        return sandbox

    async def _create_docker_sandbox(self, sandbox: Sandbox):
        """Create Docker container for sandbox."""
        config = sandbox.config

        cmd = [
            "docker", "run", "-d",
            "--name", f"polli_sandbox_{sandbox.id}",
            "-v", f"{sandbox.workspace_path}:/workspace",
            "-w", "/workspace",
            "--memory", config.memory_limit,
            "--cpus", str(config.cpu_limit),
        ]

        if not config.network_enabled:
            cmd.extend(["--network", "none"])

        cmd.extend([config.image, "tail", "-f", "/dev/null"])

        result = await self._run_host_command(cmd)
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to create container: {result.stderr}")

        sandbox.container_id = result.stdout.strip()

    async def _create_local_sandbox(self, sandbox: Sandbox):
        """Create local sandbox (no Docker)."""
        # Just use the workspace directory
        pass

    async def _clone_repo(self, sandbox: Sandbox, repo_url: str, branch: str):
        """Clone repository into sandbox."""
        # Use GitHub token if available for private repos
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        if token and "github.com" in repo_url:
            # Inject token into URL
            if repo_url.startswith("https://"):
                repo_url = repo_url.replace("https://", f"https://x-access-token:{token}@")

        clone_cmd = f"git clone --depth 1 --branch {branch} {repo_url} ."
        result = await self.execute(sandbox.id, clone_cmd, timeout=120)

        if result.exit_code != 0:
            # Try without branch (might not exist)
            clone_cmd = f"git clone --depth 1 {repo_url} . && git checkout -b {branch}"
            result = await self.execute(sandbox.id, clone_cmd, timeout=120)

        if result.exit_code != 0:
            raise RuntimeError(f"Failed to clone repo: {result.stderr}")

        logger.info(f"Cloned {repo_url}:{branch} into sandbox {sandbox.id}")

    async def execute(
        self,
        sandbox_id: str,
        command: str,
        timeout: Optional[int] = None,
        env: Optional[dict] = None,
    ) -> CommandResult:
        """
        Execute a command in the sandbox.

        Args:
            sandbox_id: Sandbox ID
            command: Command to execute
            timeout: Timeout in seconds (default from config)
            env: Additional environment variables

        Returns:
            CommandResult with output and exit code
        """
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=f"Sandbox {sandbox_id} not found",
            )

        timeout = timeout or sandbox.config.timeout
        start_time = asyncio.get_event_loop().time()

        if self.use_docker:
            result = await self._execute_docker(sandbox, command, timeout, env)
        else:
            result = await self._execute_local(sandbox, command, timeout, env)

        result.duration = asyncio.get_event_loop().time() - start_time
        return result

    async def _execute_docker(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int,
        env: Optional[dict],
    ) -> CommandResult:
        """Execute command in Docker container."""
        cmd = ["docker", "exec"]

        if env:
            for key, value in env.items():
                cmd.extend(["-e", f"{key}={value}"])

        cmd.extend([f"polli_sandbox_{sandbox.id}", "sh", "-c", command])

        return await self._run_host_command(cmd, timeout)

    async def _execute_local(
        self,
        sandbox: Sandbox,
        command: str,
        timeout: int,
        env: Optional[dict],
    ) -> CommandResult:
        """Execute command locally in workspace directory."""
        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=sandbox.workspace_path,
                env=full_env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout
                )
                return CommandResult(
                    exit_code=proc.returncode or 0,
                    stdout=stdout.decode(errors="replace"),
                    stderr=stderr.decode(errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                return CommandResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                    timed_out=True,
                )

        except Exception as e:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=str(e),
            )

    async def _run_host_command(self, cmd: list[str], timeout: int = 60) -> CommandResult:
        """Run a command on the host system."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout
                )
                return CommandResult(
                    exit_code=proc.returncode or 0,
                    stdout=stdout.decode(errors="replace"),
                    stderr=stderr.decode(errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                return CommandResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout}s",
                    timed_out=True,
                )

        except Exception as e:
            return CommandResult(
                exit_code=1,
                stdout="",
                stderr=str(e),
            )

    async def read_file(self, sandbox_id: str, path: str) -> str:
        """
        Read a file from the sandbox, falling back to local repo if not found.

        This allows the agent to read files without copying them first.
        """
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            raise ValueError(f"Sandbox {sandbox_id} not found")

        file_path = sandbox.workspace_path / path.lstrip("/")

        # Try sandbox first
        if file_path.exists():
            return file_path.read_text()

        # Fallback to local repo (read-only)
        if self.local_repo_path:
            local_file = self.local_repo_path / path.lstrip("/")
            if local_file.exists():
                return local_file.read_text()

        raise FileNotFoundError(f"File not found: {path}")

    async def write_file(self, sandbox_id: str, path: str, content: str):
        """
        Write a file to the sandbox.

        If the file doesn't exist in sandbox but exists in local repo,
        the directory structure is auto-copied to preserve git context.
        """
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            raise ValueError(f"Sandbox {sandbox_id} not found")

        file_path = sandbox.workspace_path / path.lstrip("/")

        # Auto-copy from local repo if file doesn't exist in sandbox
        if not file_path.exists() and self.local_repo_path:
            await self._ensure_file_context(sandbox, path)

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    async def _ensure_file_context(self, sandbox: Sandbox, path: str):
        """
        Copy file and necessary context from local repo to sandbox.

        Copies the file's parent directory to preserve sibling files
        that might be needed for imports/context.
        """
        if not self.local_repo_path:
            return

        local_file = self.local_repo_path / path.lstrip("/")
        if not local_file.exists():
            return

        # Copy the parent directory to preserve module context
        local_parent = local_file.parent
        sandbox_parent = sandbox.workspace_path / local_parent.relative_to(self.local_repo_path)

        if not sandbox_parent.exists():
            sandbox_parent.mkdir(parents=True, exist_ok=True)

            # Copy sibling files (same directory)
            for item in local_parent.iterdir():
                if item.is_file():
                    dest = sandbox_parent / item.name
                    shutil.copy2(item, dest)
                    logger.debug(f"Auto-copied {item.name} to sandbox")

        logger.info(f"Auto-copied context for {path} to sandbox {sandbox.id}")

    async def list_files(self, sandbox_id: str, path: str = ".", pattern: str = "*") -> list[str]:
        """
        List files in sandbox directory, merging with local repo.

        Files in sandbox take precedence over local repo files.
        """
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            raise ValueError(f"Sandbox {sandbox_id} not found")

        files = set()

        # List from sandbox
        sandbox_dir = sandbox.workspace_path / path.lstrip("/")
        if sandbox_dir.exists():
            for item in sandbox_dir.rglob(pattern):
                if item.is_file():
                    files.add(str(item.relative_to(sandbox.workspace_path)))

        # Also list from local repo (if available)
        if self.local_repo_path:
            local_dir = self.local_repo_path / path.lstrip("/")
            if local_dir.exists():
                for item in local_dir.rglob(pattern):
                    if item.is_file():
                        files.add(str(item.relative_to(self.local_repo_path)))

        return sorted(files)[:100]  # Limit to 100 files

    async def destroy(self, sandbox_id: str):
        """Destroy a sandbox and clean up resources."""
        sandbox = self.sandboxes.pop(sandbox_id, None)
        if not sandbox:
            return

        if self.use_docker and sandbox.container_id:
            # Stop and remove container
            await self._run_host_command(["docker", "stop", f"polli_sandbox_{sandbox.id}"])
            await self._run_host_command(["docker", "rm", f"polli_sandbox_{sandbox.id}"])

        # Remove workspace directory
        if sandbox.workspace_path.exists():
            shutil.rmtree(sandbox.workspace_path, ignore_errors=True)

        logger.info(f"Destroyed sandbox {sandbox_id}")

    async def _periodic_cleanup(self):
        """Periodically clean up old sandboxes."""
        while True:
            try:
                await asyncio.sleep(300)  # Every 5 minutes

                now = datetime.utcnow()
                max_age = timedelta(hours=1)

                for sandbox_id, sandbox in list(self.sandboxes.items()):
                    if now - sandbox.created_at > max_age:
                        logger.info(f"Auto-cleaning old sandbox {sandbox_id}")
                        await self.destroy(sandbox_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in sandbox cleanup: {e}")

    def get_workspace_path(self, sandbox_id: str) -> Optional[Path]:
        """Get the workspace path for a sandbox."""
        sandbox = self.sandboxes.get(sandbox_id)
        return sandbox.workspace_path if sandbox else None


# Global sandbox manager instance
# Uses embeddings repo as local repo for on-demand file copy
sandbox_manager = SandboxManager(
    use_docker=os.getenv("SANDBOX_ENABLED", "false").lower() == "true",
    local_repo_path=os.getenv("REPO_PATH"),  # Same path used for embeddings
)
