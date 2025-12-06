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

from .session_embeddings import session_embeddings_manager

logger = logging.getLogger(__name__)


@dataclass
class SandboxConfig:
    """Configuration for a sandbox."""
    image: str = "node:20-slim"  # Node.js for ccr
    memory_limit: str = "2g"
    cpu_limit: float = 2.0
    timeout: int = 300  # 5 minutes default
    network_enabled: bool = True  # For npm install etc


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

    # User who initiated the sandbox (for destruction confirmation)
    initiated_by: Optional[str] = None
    initiated_source: Optional[str] = None  # "discord" or "github"

    # Pending destruction confirmation
    pending_destruction: bool = False


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
        initiated_by: Optional[str] = None,
        initiated_source: Optional[str] = None,
    ) -> Sandbox:
        """
        Create a new sandbox.

        Args:
            repo_url: Git repository URL to clone (optional)
            branch: Branch to checkout
            config: Sandbox configuration
            initiated_by: Username of who started this sandbox
            initiated_source: Source platform ("discord" or "github")

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
            initiated_by=initiated_by,
            initiated_source=initiated_source,
        )

        if self.use_docker:
            await self._create_docker_sandbox(sandbox)
        else:
            await self._create_local_sandbox(sandbox)

        # Add to sandboxes BEFORE clone so execute() can find it
        self.sandboxes[sandbox_id] = sandbox

        # Clone repo if provided
        if repo_url:
            await self._clone_repo(sandbox, repo_url, branch)

        # Create session embeddings for this sandbox
        await session_embeddings_manager.create_session(sandbox_id)
        logger.info(f"Created sandbox {sandbox_id} for {repo_url or 'empty'} (by {initiated_by})")

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

        # Install git (node:20-slim doesn't include it)
        install_git = await self._run_host_command([
            "docker", "exec", f"polli_sandbox_{sandbox.id}",
            "sh", "-c", "apt-get update && apt-get install -y git --no-install-recommends && rm -rf /var/lib/apt/lists/*"
        ], timeout=120)

        if install_git.exit_code != 0:
            logger.warning(f"Failed to install git in container: {install_git.stderr}")

    async def _create_local_sandbox(self, sandbox: Sandbox):
        """Create local sandbox (no Docker)."""
        # Just use the workspace directory
        pass

    async def _clone_repo(self, sandbox: Sandbox, repo_url: str, branch: str):
        """
        Setup repository in sandbox.

        First tries to copy from local cache (fast), falls back to git clone.
        Local cache is at /root/Polly/data/repo/{owner}_{repo}/
        """
        # Try to use local repo cache first (much faster than cloning 600MB+)
        if "github.com" in repo_url:
            # Extract owner/repo from URL
            # https://github.com/owner/repo.git -> owner_repo
            parts = repo_url.rstrip("/").rstrip(".git").split("/")
            if len(parts) >= 2:
                owner, repo_name = parts[-2], parts[-1]
                local_cache = f"/root/Polly/data/repo/{owner}_{repo_name}"

                # Check if local cache exists and copy it
                check_cmd = f"test -d {local_cache} && cp -r {local_cache}/. /workspace/"
                result = await self.execute(sandbox.id, check_cmd, timeout=60)

                if result.exit_code == 0:
                    # Setup git and checkout branch
                    setup_cmd = f"cd /workspace && git checkout {branch} 2>/dev/null || git checkout -b {branch}"
                    await self.execute(sandbox.id, setup_cmd, timeout=30)
                    logger.info(f"Copied {owner}/{repo_name} from local cache to sandbox {sandbox.id}")
                    return

        # Fallback to git clone if local cache not available
        token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        if token and "github.com" in repo_url:
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

        Also automatically indexes the file in session embeddings.
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

        # Index in session embeddings (async, non-blocking)
        # Only index code files
        code_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".c", ".cpp", ".h"}
        if Path(path).suffix.lower() in code_extensions:
            try:
                await session_embeddings_manager.index_file(sandbox_id, path, content)
            except Exception as e:
                logger.warning(f"Failed to index {path} in session embeddings: {e}")

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

    async def destroy(self, sandbox_id: str, force: bool = False):
        """
        Destroy a sandbox and clean up resources.

        Args:
            sandbox_id: ID of sandbox to destroy
            force: If True, skip confirmation check
        """
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            return

        # Check if pending confirmation (unless forced)
        if sandbox.pending_destruction and not force:
            logger.info(f"Sandbox {sandbox_id} already pending destruction confirmation")
            return

        # Remove from tracking
        self.sandboxes.pop(sandbox_id, None)

        if self.use_docker and sandbox.container_id:
            # Stop and remove container
            await self._run_host_command(["docker", "stop", f"polli_sandbox_{sandbox.id}"])
            await self._run_host_command(["docker", "rm", f"polli_sandbox_{sandbox.id}"])

        # Remove workspace directory
        if sandbox.workspace_path.exists():
            shutil.rmtree(sandbox.workspace_path, ignore_errors=True)

        # Clean up session embeddings
        await session_embeddings_manager.destroy_session(sandbox_id)

        logger.info(f"Destroyed sandbox {sandbox_id}")

    async def request_destruction(self, sandbox_id: str) -> dict:
        """
        Request sandbox destruction (pending user confirmation).

        Returns sandbox info for confirmation message.
        """
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            return {"error": "Sandbox not found"}

        sandbox.pending_destruction = True

        # Get session stats for user info
        session = session_embeddings_manager.get_session(sandbox_id)
        session_stats = session.get_stats() if session else {}

        return {
            "sandbox_id": sandbox_id,
            "initiated_by": sandbox.initiated_by,
            "initiated_source": sandbox.initiated_source,
            "created_at": sandbox.created_at.isoformat(),
            "repo_url": sandbox.repo_url,
            "files_modified": session_stats.get("files_indexed", 0),
            "chunks_indexed": session_stats.get("total_chunks", 0),
        }

    async def confirm_destruction(self, sandbox_id: str, confirmed_by: str) -> bool:
        """
        Confirm sandbox destruction by authorized user.

        Only the user who initiated the sandbox can confirm destruction.
        """
        sandbox = self.sandboxes.get(sandbox_id)
        if not sandbox:
            return False

        # Check authorization
        if sandbox.initiated_by and sandbox.initiated_by.lower() != confirmed_by.lower():
            logger.warning(
                f"Unauthorized destruction attempt: {confirmed_by} tried to destroy "
                f"sandbox {sandbox_id} owned by {sandbox.initiated_by}"
            )
            return False

        await self.destroy(sandbox_id, force=True)
        return True

    async def cancel_destruction(self, sandbox_id: str):
        """Cancel pending sandbox destruction."""
        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox:
            sandbox.pending_destruction = False
            logger.info(f"Cancelled destruction of sandbox {sandbox_id}")

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

    async def search_code(
        self,
        sandbox_id: str,
        query: str,
        top_k: int = 10,
        include_global: bool = True,
    ) -> list[dict]:
        """
        Search code using session embeddings (and optionally global).

        Args:
            sandbox_id: Sandbox ID
            query: Search query
            top_k: Number of results
            include_global: Whether to also search global repo embeddings

        Returns:
            List of matching code chunks with file paths and similarity scores
        """
        if include_global:
            return await session_embeddings_manager.search_combined(sandbox_id, query, top_k)
        else:
            return await session_embeddings_manager.search_session(sandbox_id, query, top_k)

    def get_session_stats(self, sandbox_id: str) -> dict:
        """Get session embedding stats for a sandbox."""
        session = session_embeddings_manager.get_session(sandbox_id)
        if session:
            return session.get_stats()
        return {"error": "No session found"}


# Global sandbox manager instance
# Uses embeddings repo as local repo for on-demand file copy
# Import config here to avoid circular imports at module level
def _create_sandbox_manager():
    from ...config import config
    return SandboxManager(
        use_docker=config.sandbox_enabled,
        local_repo_path=os.getenv("REPO_PATH"),  # Same path used for embeddings
    )

# Lazy initialization to avoid import issues
_sandbox_manager = None

def get_sandbox_manager() -> SandboxManager:
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = _create_sandbox_manager()
    return _sandbox_manager

# For backward compatibility - will be initialized on first access
class _LazySandboxManager:
    """Lazy proxy for sandbox_manager to allow config to load first."""
    def __getattr__(self, name):
        return getattr(get_sandbox_manager(), name)

sandbox_manager = _LazySandboxManager()
