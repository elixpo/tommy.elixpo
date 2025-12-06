"""
GitHub Code tool handler for Discord bot integration.

Uses ccr (code router) as the coding engine.

Architecture:
- Bot AI handles user intent and conversation
- ccr handles all actual coding work (read, edit, test, git)
- Single Discord embed updates in real-time (no message spam)
- Sandbox stays alive for follow-up commands

Available actions:
- task: Full coding workflow
- status: Check running task status
- create_branch: Create a new branch
- edit_file: Edit a file directly
- commit: Commit staged changes
- push: Push to remote
- open_pr: Open a pull request
- delete_branch: Delete a branch
- list_branches: List branches
- read_file: Read file contents
- list_files: List files in directory
"""

import asyncio
import logging
import os
from typing import Optional, Any
from datetime import datetime

import discord

from ..sandbox import sandbox_manager, Sandbox
from ..claude_code_agent import get_claude_code_agent, ClaudeCodeResult, parse_todos_from_output, TodoItem
from ..embed_builder import ProgressEmbedManager, StepStatus
from ..output_summarizer import output_summarizer

logger = logging.getLogger(__name__)

# Store running tasks for status checks
_running_tasks: dict[str, dict] = {}


async def tool_polly_agent(
    action: str,
    task: Optional[str] = None,
    repo: str = "pollinations/pollinations",
    branch: str = "main",
    task_id: Optional[str] = None,
    # Git operation parameters
    new_branch: Optional[str] = None,
    file_path: Optional[str] = None,
    file_content: Optional[str] = None,
    old_content: Optional[str] = None,  # For edit_file search/replace
    commit_message: Optional[str] = None,
    pr_title: Optional[str] = None,
    pr_body: Optional[str] = None,
    base_branch: Optional[str] = None,  # For PRs
    pattern: Optional[str] = None,  # For list_files
    # Discord context (injected by bot.py)
    discord_channel: Optional[discord.TextChannel] = None,
    discord_user_name: Optional[str] = None,
    # Admin flag - MUST be explicitly set by bot.py wrapper
    _is_admin: bool = False,
    **kwargs
) -> dict:
    """
    GitHub Code tool handler - flexible git operations.

    Architecture:
        YOU drive the workflow - the code agent just executes coding tasks.
        After 'task' action completes, YOU decide what to do next based on context.
        There are NO predefined steps - use your judgment to:
        - Ask users questions when their input adds value
        - Run tests/builds via sandbox if relevant
        - Create PRs when changes are ready
        - Send follow-up prompts to the code agent for more work

    Actions:
        task: Run coding task (returns sandbox_id for follow-ups)
        status: Check running task status
        run_in_sandbox: Execute ANY command in sandbox (tests, builds, etc)
        read_sandbox_file: Read file from sandbox
        write_sandbox_file: Write file to sandbox
        destroy_sandbox: Destroy sandbox when done

        Git Operations (via GitHub API):
        create_branch: Create a new branch
        edit_file: Edit a file directly
        read_file: Read file contents
        list_files: List files
        commit: (no-op - commits auto-created)
        push: (no-op - pushes auto-done)
        open_pr: Create PR
        delete_branch: Delete a branch
        list_branches: List all branches

    Response fields:
        _ai_hint: Guidance for YOU (Gemini) on what to consider next.
                  NOT shown to users - just helps you decide.

    Args:
        action: Action to perform
        task: Task description (for task action)
        repo: Repository (owner/repo)
        branch: Working branch
        new_branch: Branch name for create/delete
        file_path: Path to file for read/edit
        file_content: New content for file
        old_content: Old content to find and replace
        commit_message: Message for commits
        pr_title: PR title
        pr_body: PR description
        base_branch: Base branch for PR
        pattern: Glob pattern for list_files
        _is_admin: Admin flag - MUST be set by bot.py (default False = blocked)

    Returns:
        Result dict with status, details, and _ai_hint for follow-up guidance
    """
    # SECURITY: Admin check - code agent can modify repos
    # The _is_admin flag MUST be explicitly set True by the bot.py wrapper
    # Default is False, so direct calls without the wrapper are blocked
    if not _is_admin:
        return {
            "error": "Code agent requires admin permissions. This tool can modify repository code, create branches, and open PRs - ask a team member with admin access!"
        }

    try:
        # Status check (no sandbox needed)
        if action == "status":
            return await _handle_status(task_id)

        # List branches (uses GitHub API directly)
        if action == "list_branches":
            return await _handle_list_branches(repo)

        # task action uses code agent
        if action == "task":
            return await _handle_code_task(
                task=task,
                repo=repo,
                branch=branch,
                channel=discord_channel,
                user_name=discord_user_name or "Unknown",
            )

        # Sandbox operations - work with existing sandbox
        if action == "run_in_sandbox":
            return await _handle_run_in_sandbox(kwargs.get("sandbox_id"), kwargs.get("command"))

        if action == "read_sandbox_file":
            return await _handle_read_sandbox_file(kwargs.get("sandbox_id"), file_path)

        if action == "write_sandbox_file":
            return await _handle_write_sandbox_file(kwargs.get("sandbox_id"), file_path, file_content)

        if action == "destroy_sandbox":
            return await _handle_destroy_sandbox(kwargs.get("sandbox_id"), discord_user_name)

        # Git operations - use GitHub API directly
        if action == "create_branch":
            return await _handle_create_branch(repo, branch, new_branch)

        if action == "delete_branch":
            return await _handle_delete_branch(repo, new_branch)

        if action == "read_file":
            return await _handle_read_file(repo, branch, file_path)

        if action == "list_files":
            return await _handle_list_files(repo, branch, pattern)

        if action == "edit_file":
            return await _handle_edit_file(repo, branch, file_path, file_content, old_content)

        if action == "commit":
            return await _handle_commit(repo, branch, commit_message)

        if action == "push":
            return await _handle_push(repo, branch)

        if action == "open_pr":
            return await _handle_open_pr(repo, branch, base_branch, pr_title, pr_body)

        return {"error": f"Unknown action: {action}. Available: task, status, run_in_sandbox, read_sandbox_file, write_sandbox_file, destroy_sandbox, list_branches, create_branch, delete_branch, read_file, list_files, edit_file, commit, push, open_pr"}

    except Exception as e:
        logger.exception("Error in polly_agent tool")
        return {"error": str(e)}


async def _handle_status(task_id: Optional[str]) -> dict:
    """Handle status check for a running task."""
    if not task_id:
        # Return all running tasks
        if not _running_tasks:
            return {"message": "No tasks currently running."}

        tasks_info = []
        for tid, info in _running_tasks.items():
            tasks_info.append(f"- **{tid}**: {info['task'][:50]}... ({info['phase']})")

        return {
            "message": f"**Running Tasks:**\n" + "\n".join(tasks_info)
        }

    # Check specific task
    task_info = _running_tasks.get(task_id)
    if not task_info:
        return {"error": f"Task {task_id} not found"}

    elapsed = (datetime.utcnow() - task_info["started_at"]).total_seconds()

    return {
        "task_id": task_id,
        "task": task_info["task"],
        "phase": task_info["phase"],
        "elapsed_seconds": elapsed,
        "messages": task_info.get("messages", [])[-5:],  # Last 5 messages
    }


async def _handle_run_in_sandbox(sandbox_id: Optional[str], command: Optional[str]) -> dict:
    """Run a command in an existing sandbox."""
    if not sandbox_id:
        return {"error": "sandbox_id is required"}
    if not command:
        return {"error": "command is required"}

    from ..sandbox import sandbox_manager

    sandbox = sandbox_manager.sandboxes.get(sandbox_id)
    if not sandbox:
        return {"error": f"Sandbox {sandbox_id} not found. It may have been destroyed or expired."}

    result = await sandbox_manager.execute(sandbox_id, command, timeout=120)

    return {
        "success": result.exit_code == 0,
        "sandbox_id": sandbox_id,
        "command": command,
        "exit_code": result.exit_code,
        "stdout": result.stdout[:4000] if result.stdout else "",
        "stderr": result.stderr[:2000] if result.stderr else "",
        "timed_out": result.timed_out,
        "duration": result.duration,
    }


async def _handle_read_sandbox_file(sandbox_id: Optional[str], file_path: Optional[str]) -> dict:
    """Read a file from an existing sandbox."""
    if not sandbox_id:
        return {"error": "sandbox_id is required"}
    if not file_path:
        return {"error": "file_path is required"}

    from ..sandbox import sandbox_manager

    sandbox = sandbox_manager.sandboxes.get(sandbox_id)
    if not sandbox:
        return {"error": f"Sandbox {sandbox_id} not found. It may have been destroyed or expired."}

    try:
        content = await sandbox_manager.read_file(sandbox_id, file_path)
        return {
            "success": True,
            "sandbox_id": sandbox_id,
            "file_path": file_path,
            "content": content[:10000],  # Limit content size
        }
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}"}
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}


async def _handle_write_sandbox_file(sandbox_id: Optional[str], file_path: Optional[str], content: Optional[str]) -> dict:
    """Write a file to an existing sandbox."""
    if not sandbox_id:
        return {"error": "sandbox_id is required"}
    if not file_path:
        return {"error": "file_path is required"}
    if content is None:
        return {"error": "file_content is required"}

    from ..sandbox import sandbox_manager

    sandbox = sandbox_manager.sandboxes.get(sandbox_id)
    if not sandbox:
        return {"error": f"Sandbox {sandbox_id} not found. It may have been destroyed or expired."}

    try:
        await sandbox_manager.write_file(sandbox_id, file_path, content)
        return {
            "success": True,
            "sandbox_id": sandbox_id,
            "file_path": file_path,
            "message": f"File written: {file_path}",
        }
    except Exception as e:
        return {"error": f"Failed to write file: {e}"}


async def _handle_destroy_sandbox(sandbox_id: Optional[str], user_name: Optional[str]) -> dict:
    """Destroy a sandbox - only the creator can confirm."""
    if not sandbox_id:
        return {"error": "sandbox_id is required"}

    from ..sandbox import sandbox_manager

    sandbox = sandbox_manager.sandboxes.get(sandbox_id)
    if not sandbox:
        return {"error": f"Sandbox {sandbox_id} not found. It may have already been destroyed."}

    # Check if user is the creator
    if sandbox.initiated_by and user_name:
        if sandbox.initiated_by.lower() != user_name.lower():
            return {
                "error": f"Only {sandbox.initiated_by} can destroy this sandbox.",
                "sandbox_id": sandbox_id,
                "initiated_by": sandbox.initiated_by,
            }

    await sandbox_manager.destroy(sandbox_id, force=True)

    return {
        "success": True,
        "sandbox_id": sandbox_id,
        "message": f"Sandbox {sandbox_id} has been destroyed.",
    }


async def _handle_code_task(
    task: str,
    repo: str,
    branch: str,
    channel: Optional[discord.TextChannel] = None,
    user_name: Optional[str] = None,
) -> dict:
    """
    Handle coding task via ccr.

    This architecture:
    - Creates sandbox with repo cloned
    - Installs ccr
    - Runs the task prompt
    - Returns results with sandbox still running for follow-ups

    The bot AI handles:
    - Building task context
    - Summarizing output for Discord
    - Managing user interactions (pause, resume, etc.)
    """
    if not task:
        return {"error": "Task description is required"}

    import uuid
    task_id = str(uuid.uuid4())[:8]

    # Track the task
    _running_tasks[task_id] = {
        "task": task,
        "repo": repo,
        "branch": branch,
        "phase": "coding",
        "started_at": datetime.utcnow(),
        "messages": [],
    }

    # Create progress embed if Discord channel available
    embed_manager: Optional[ProgressEmbedManager] = None
    dynamic_todo_indices: dict[str, int] = {}  # Track todo content -> step index
    if channel:
        embed_manager = ProgressEmbedManager(channel)
        try:
            await embed_manager.start(
                title=f"Working on: {task[:50]}...",
                description=f"Repository: `{repo}:{branch}`",
                repo_url=f"https://github.com/{repo}",
            )
            # Initial setup steps
            embed_manager.add_step("Creating sandbox")
            embed_manager.add_step("Setting up environment")
            await embed_manager.update()
        except Exception as e:
            logger.warning(f"Failed to create progress embed: {e}")
            embed_manager = None

    try:
        # Step 1: Create sandbox
        if embed_manager:
            embed_manager.start_step(0)
            embed_manager.set_status("Creating sandbox environment...")
            await embed_manager.update()

        sandbox = await sandbox_manager.create(
            repo_url=f"https://github.com/{repo}.git",
            branch=branch,
            initiated_by=user_name,
            initiated_source="discord" if channel else None,
        )

        _running_tasks[task_id]["sandbox_id"] = sandbox.id
        _running_tasks[task_id]["messages"].append(f"Sandbox {sandbox.id} created")

        if embed_manager:
            embed_manager.complete_step(0, f"Sandbox {sandbox.id}")
            embed_manager.start_step(1)
            embed_manager.set_status("Setting up coding environment...")
            await embed_manager.update()

        # Step 2-3: Run task via the agent
        agent = get_claude_code_agent()

        # Progress callback for the agent - dynamically updates todos
        async def on_progress(message: str):
            nonlocal dynamic_todo_indices
            _running_tasks[task_id]["messages"].append(message)

            if embed_manager:
                # Check if setup is complete
                if "starting" in message.lower() or "🚀" in message:
                    embed_manager.complete_step(1)
                    embed_manager.set_status("Working on task...")
                    await embed_manager.update()
                    return

                # Try to parse todos from accumulated output
                accumulated = "\n".join(_running_tasks[task_id]["messages"])
                todos = parse_todos_from_output(accumulated)

                # Update embed with parsed todos
                for todo in todos:
                    if todo.content not in dynamic_todo_indices:
                        # Add new todo step
                        idx = embed_manager.add_step(todo.content)
                        dynamic_todo_indices[todo.content] = idx

                    idx = dynamic_todo_indices[todo.content]
                    if todo.status == "completed":
                        embed_manager.complete_step(idx)
                    elif todo.status == "in_progress":
                        embed_manager.start_step(idx)
                    elif todo.status == "failed":
                        embed_manager.fail_step(idx)

                # Update status message
                embed_manager.set_status(message[:100] if len(message) > 100 else message)
                await embed_manager.update()

        result: ClaudeCodeResult = await agent.run_task(
            sandbox_id=sandbox.id,
            prompt=task,
            on_progress=on_progress,
        )

        # Final embed update with all todos from result
        if embed_manager:
            # Add any final todos not caught during streaming
            for todo in result.todos:
                if todo.content not in dynamic_todo_indices:
                    idx = embed_manager.add_step(todo.content)
                    dynamic_todo_indices[todo.content] = idx

                idx = dynamic_todo_indices[todo.content]
                if todo.status == "completed":
                    embed_manager.complete_step(idx)
                elif todo.status == "in_progress":
                    embed_manager.complete_step(idx)  # Mark as done at end
                elif todo.status == "failed":
                    embed_manager.fail_step(idx)

            if result.success:
                status_msg = f"Done! {len(result.files_changed)} file(s) changed"
                if result.pr_url:
                    status_msg += f" | [PR]({result.pr_url})"
                embed_manager.set_status(status_msg)
            else:
                embed_manager.set_status(f"Error: {result.error or 'Task failed'}")

            await embed_manager.finish(success=result.success)

        # Update tracking
        _running_tasks[task_id]["phase"] = "complete" if result.success else "failed"
        _running_tasks[task_id]["messages"].append(f"Duration: {result.duration_seconds}s")

        # Summarize output for AI to present
        summary = await output_summarizer.summarize_with_ai(
            result.output,
            task_context=task,
            max_length=200
        )

        # Include todos in response
        todos_summary = [{"content": t.content, "status": t.status} for t in result.todos]

        return {
            "success": result.success,
            "task_id": task_id,
            "sandbox_id": sandbox.id,
            "task": task,
            "repo": repo,
            "branch": branch,
            "summary": summary,
            "files_changed": result.files_changed,
            "commits_made": result.commits_made,
            "pr_url": result.pr_url,
            "todos": todos_summary,
            "output_preview": result.output[-1000:] if result.output else "",
            "duration": result.duration_seconds,
            "error": result.error,
            # Hint for AI - guide Gemini to make smart decisions
            "_ai_hint": (
                "SANDBOX ACTIVE: You can send follow-up tasks via this sandbox. "
                "The sandbox runs in a full Linux environment and can execute ANY commands (tests, builds, linting, etc). "
                "\n\nYOU DECIDE what to do next based on context - there are NO predefined steps. Consider: "
                "\n- Did the task complete successfully? Summarize changes for the user. "
                "\n- Should tests be run? Only if relevant to changes or user requested. "
                "\n- Is user input needed? ASK if: multiple valid approaches exist, changes are significant, or you're unsure about direction. "
                "\n- Ready for PR? Offer to create one if changes look good. "
                "\n- More work needed? You can send another task prompt. "
                "\n\nActions available: run_in_sandbox (any command), read_sandbox_file, write_sandbox_file, open_pr, destroy_sandbox. "
                "Use your judgment - engage users when their input adds value, not for every decision."
            )
        }

    except Exception as e:
        _running_tasks[task_id]["phase"] = "failed"
        _running_tasks[task_id]["messages"].append(f"Error: {e}")
        logger.exception("Task failed")

        if embed_manager:
            embed_manager.set_status(f"Error: {e}")
            await embed_manager.finish(success=False)

        # Get sandbox_id if it was created
        sandbox_id = _running_tasks.get(task_id, {}).get("sandbox_id")

        return {
            "success": False,
            "error": str(e),
            "task_id": task_id,
            "sandbox_id": sandbox_id,
            "task": task,  # Include original task for context
            "repo": repo,
            "branch": branch,
            "_ai_hint": (
                f"Task failed with error: {e}. "
                "You have all the context - DO NOT ask the user for repo/issue info you already have. "
                "Consider: retry the task, try a simpler approach, or explain the error and ask how to proceed."
            )
        }

    finally:
        asyncio.create_task(_cleanup_task(task_id, delay=300))


# =============================================================================
# Flexible Git Operation Handlers - Use GitHub API directly (no sandbox needed)
# =============================================================================

async def _get_github_token() -> str:
    """Get GitHub token from environment or auth manager."""
    from ...github_auth import github_app_auth
    from ....config import config

    # Try GitHub App first
    if config.use_github_app and github_app_auth:
        try:
            return await github_app_auth.get_token()
        except Exception:
            pass

    # Fall back to PAT
    return config.github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or ""


async def _github_api(method: str, endpoint: str, data: dict = None) -> tuple[int, dict]:
    """Make GitHub API request using shared session from github_manager."""
    import aiohttp
    from ...github import github_manager

    token = await _get_github_token()
    if not token:
        return 401, {"error": "No GitHub token configured"}

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    url = f"https://api.github.com{endpoint}"

    # Use shared session from github_manager (connection pooling)
    session = await github_manager.get_session()
    async with session.request(
        method, url, headers=headers, json=data,
        timeout=aiohttp.ClientTimeout(total=30)
    ) as response:
        try:
            result = await response.json()
        except:
            result = {"message": await response.text()}
        return response.status, result


async def _handle_list_branches(repo: str) -> dict:
    """List all branches in a repository using REST API."""
    # Get default branch first
    status, repo_data = await _github_api("GET", f"/repos/{repo}")
    default_branch = repo_data.get("default_branch", "main") if status == 200 else "main"

    # List branches
    status, branches = await _github_api("GET", f"/repos/{repo}/branches?per_page=100")
    if status != 200:
        return {"error": f"Failed to list branches: {branches.get('message', status)}"}

    branch_list = [b["name"] for b in branches]

    # Format with default branch indicator
    lines = []
    for b in branches[:20]:
        marker = " (default)" if b["name"] == default_branch else ""
        lines.append(f"- {b['name']}{marker}")

    return {
        "success": True,
        "branches": branch_list,
        "default_branch": default_branch,
        "message": f"**Branches in {repo}:**\n" + "\n".join(lines)
    }


async def _handle_create_branch(repo: str, base_branch: str, new_branch: Optional[str]) -> dict:
    """Create a new branch from base branch using GitHub API."""
    if not new_branch:
        return {"error": "new_branch parameter is required"}

    # Get the SHA of the base branch
    status, data = await _github_api("GET", f"/repos/{repo}/git/ref/heads/{base_branch}")
    if status != 200:
        return {"error": f"Base branch '{base_branch}' not found: {data.get('message', status)}"}

    base_sha = data["object"]["sha"]

    # Create the new branch
    status, data = await _github_api(
        "POST",
        f"/repos/{repo}/git/refs",
        {"ref": f"refs/heads/{new_branch}", "sha": base_sha}
    )

    if status == 201:
        return {
            "success": True,
            "branch": new_branch,
            "base": base_branch,
            "message": f"✅ Created branch `{new_branch}` from `{base_branch}`"
        }
    elif status == 422:
        return {"error": f"Branch `{new_branch}` already exists"}
    return {"error": f"Failed to create branch: {data.get('message', status)}"}


async def _handle_delete_branch(repo: str, branch_name: Optional[str]) -> dict:
    """Delete a branch using GitHub API."""
    if not branch_name:
        return {"error": "new_branch parameter is required (the branch to delete)"}

    # Safety check - don't delete main/master
    if branch_name in ("main", "master"):
        return {"error": "Cannot delete main/master branch!"}

    status, data = await _github_api("DELETE", f"/repos/{repo}/git/refs/heads/{branch_name}")

    if status == 204:
        return {
            "success": True,
            "branch": branch_name,
            "message": f"✅ Deleted branch `{branch_name}`"
        }
    elif status == 422:
        return {"error": f"Branch `{branch_name}` not found or protected"}
    return {"error": f"Failed to delete branch: {data.get('message', status)}"}


async def _handle_read_file(repo: str, branch: str, file_path: Optional[str]) -> dict:
    """Read a file from the repository using GitHub API."""
    if not file_path:
        return {"error": "file_path parameter is required"}

    status, data = await _github_api("GET", f"/repos/{repo}/contents/{file_path}?ref={branch}")

    if status == 200:
        import base64
        content = base64.b64decode(data["content"]).decode("utf-8")

        # Truncate if too long for Discord
        display_content = content
        if len(display_content) > 1800:
            display_content = display_content[:1800] + "\n\n... (truncated)"

        return {
            "success": True,
            "file": file_path,
            "content": content,
            "sha": data["sha"],
            "message": f"**{file_path}:**\n```\n{display_content}\n```"
        }
    elif status == 404:
        return {"error": f"File not found: {file_path}"}
    return {"error": f"Failed to read file: {data.get('message', status)}"}


async def _handle_list_files(repo: str, branch: str, pattern: Optional[str]) -> dict:
    """List files in the repository using GitHub API."""
    # Get the tree recursively
    status, data = await _github_api("GET", f"/repos/{repo}/git/trees/{branch}?recursive=1")

    if status == 200:
        import fnmatch
        glob_pattern = pattern or "*"

        files = [item["path"] for item in data.get("tree", []) if item["type"] == "blob"]

        # Filter by pattern if provided
        if pattern:
            files = [f for f in files if fnmatch.fnmatch(f, glob_pattern)]

        return {
            "success": True,
            "pattern": glob_pattern,
            "files": files[:100],  # Limit to 100
            "message": f"**Files matching `{glob_pattern}`:**\n" + "\n".join([f"- {f}" for f in files[:30]])
        }
    return {"error": f"Failed to list files: {data.get('message', status)}"}


async def _handle_edit_file(
    repo: str,
    branch: str,
    file_path: Optional[str],
    new_content: Optional[str],
    old_content: Optional[str]
) -> dict:
    """Edit a file in the repository using GitHub API."""
    if not file_path:
        return {"error": "file_path parameter is required"}
    if not new_content:
        return {"error": "file_content parameter is required"}

    import base64

    # Get current file to get SHA (needed for update)
    status, data = await _github_api("GET", f"/repos/{repo}/contents/{file_path}?ref={branch}")

    file_sha = None
    if status == 200:
        file_sha = data["sha"]

        if old_content:
            # Search and replace mode
            current_content = base64.b64decode(data["content"]).decode("utf-8")
            if old_content not in current_content:
                return {"error": f"Could not find the specified old_content in {file_path}"}
            new_content = current_content.replace(old_content, new_content, 1)
    elif status == 404 and old_content:
        return {"error": f"File not found: {file_path}"}
    # If 404 and no old_content, we're creating a new file

    # Update/create the file
    encoded_content = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")

    update_data = {
        "message": f"Update {file_path}",
        "content": encoded_content,
        "branch": branch
    }
    if file_sha:
        update_data["sha"] = file_sha

    status, data = await _github_api("PUT", f"/repos/{repo}/contents/{file_path}", update_data)

    if status in (200, 201):
        return {
            "success": True,
            "file": file_path,
            "commit_sha": data.get("commit", {}).get("sha", "")[:8],
            "message": f"✅ Updated `{file_path}` on branch `{branch}`"
        }
    return {"error": f"Failed to edit file: {data.get('message', status)}"}


async def _handle_commit(repo: str, branch: str, message: Optional[str]) -> dict:
    """
    Commit is handled automatically by edit_file via GitHub API.
    This is a no-op that just returns info.
    """
    return {
        "success": True,
        "message": "ℹ️ Commits are created automatically when using `edit_file`. Each edit creates a commit."
    }


async def _handle_push(repo: str, branch: str) -> dict:
    """
    Push is handled automatically by GitHub API.
    This is a no-op that just returns info.
    """
    return {
        "success": True,
        "message": "ℹ️ Changes are pushed automatically when using `edit_file` via the GitHub API."
    }


async def _handle_open_pr(
    repo: str,
    head_branch: str,
    base_branch: Optional[str],
    title: Optional[str],
    body: Optional[str]
) -> dict:
    """Create a pull request using shared session."""
    if not title:
        return {"error": "pr_title parameter is required"}

    base = base_branch or "main"

    # Use shared _github_api helper (which uses shared session)
    pr_data = {
        "title": title,
        "head": head_branch,
        "base": base,
        "body": body or f"Created by Polli bot.\n\n🤖 Automated PR"
    }

    status, data = await _github_api("POST", f"/repos/{repo}/pulls", pr_data)

    if status == 201:
        return {
            "success": True,
            "pr_number": data["number"],
            "pr_url": data["html_url"],
            "message": f"✅ Created PR #{data['number']}: [{title}](<{data['html_url']}>)"
        }
    else:
        return {"error": f"Failed to create PR: {data.get('message', status)}"}


async def _cleanup_task(task_id: str, delay: int):
    """Clean up task tracking after delay."""
    await asyncio.sleep(delay)
    _running_tasks.pop(task_id, None)


# Export tool handler
TOOL_HANDLERS = {
    "polly_agent": tool_polly_agent,
}
