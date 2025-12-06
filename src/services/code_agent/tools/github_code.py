"""
GitHub Code tool handler for Discord bot integration.

This connects the CodeAgent to the Discord bot's tool calling system.
Supports interactive mode with live Discord updates and human-in-the-loop.

Available actions:
- task: Full coding workflow (plan -> code -> test -> commit/PR)
- plan: Generate implementation plan only
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

from ..agent import CodeAgent, code_agent, AgentResult, AgentPhase
from ..sandbox import sandbox_manager, Sandbox  # Only needed for task/plan actions
from ..discord_progress import (
    DiscordProgressReporter,
    HumanFeedback,
    HumanFeedbackType,
    register_reporter,
    unregister_reporter,
)

logger = logging.getLogger(__name__)

# Store running tasks for status checks
_running_tasks: dict[str, dict] = {}

# Store active interactive sessions
_interactive_sessions: dict[int, dict] = {}  # channel_id -> session info


async def tool_github_code(
    action: str,
    task: Optional[str] = None,
    repo: str = "pollinations/pollinations",
    branch: str = "main",
    create_pr: bool = False,
    max_fix_attempts: int = 5,
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
    # Discord context for interactive mode (injected by bot.py)
    discord_channel: Optional[discord.TextChannel] = None,
    discord_bot: Optional[discord.Client] = None,
    discord_user_id: Optional[int] = None,
    discord_user_name: Optional[str] = None,
    interactive: bool = True,  # Enable live updates by default
    human_review: bool = True,  # Use human review instead of AI
    # Admin flag - MUST be explicitly set by bot.py wrapper
    _is_admin: bool = False,
    **kwargs
) -> dict:
    """
    GitHub Code tool handler - flexible git operations.

    Actions:
        task: Full coding workflow (plan -> code -> test -> commit/PR)
        plan: Generate implementation plan only
        status: Check running task status

        Git Operations (flexible):
        create_branch: Create a new branch (needs: new_branch, repo, branch)
        edit_file: Edit a file (needs: file_path, file_content OR old_content+file_content)
        read_file: Read file contents (needs: file_path)
        list_files: List files (needs: pattern optional)
        commit: Commit staged changes (needs: commit_message)
        push: Push to remote
        open_pr: Create PR (needs: pr_title, pr_body optional, base_branch optional)
        delete_branch: Delete a branch (needs: new_branch)
        list_branches: List all branches

    Args:
        action: Action to perform
        task: Task description (for task/plan actions)
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
        Result dict with status and details
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

        # Full workflow actions
        if action == "plan":
            return await _handle_plan(task, repo, branch)

        if action == "task":
            if interactive and discord_channel and discord_bot:
                return await _handle_interactive_task(
                    task=task,
                    repo=repo,
                    branch=branch,
                    create_pr=create_pr,
                    max_fix_attempts=max_fix_attempts,
                    channel=discord_channel,
                    bot=discord_bot,
                    user_id=discord_user_id or 0,
                    user_name=discord_user_name or "Unknown",
                    human_review=human_review,
                )
            else:
                return await _handle_task(
                    task=task,
                    repo=repo,
                    branch=branch,
                    create_pr=create_pr,
                    max_fix_attempts=max_fix_attempts,
                )

        # Git operations - need a sandbox
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

        return {"error": f"Unknown action: {action}. Available: task, plan, status, create_branch, delete_branch, read_file, list_files, edit_file, commit, push, open_pr, list_branches"}

    except Exception as e:
        logger.exception("Error in github_code tool")
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


async def _handle_plan(task: str, repo: str, branch: str) -> dict:
    """Handle plan-only mode (no execution)."""
    if not task:
        return {"error": "Task description is required"}

    agent = CodeAgent()

    try:
        await agent.initialize()

        # Just do understanding + planning phases
        from ..sandbox import sandbox_manager

        sandbox = await sandbox_manager.create(
            repo_url=f"https://github.com/{repo}.git",
            branch=branch,
        )

        try:
            # Generate repo map
            repo_map = await agent._generate_repo_map(sandbox)

            # Create state for planning
            from ..agent import AgentState
            state = AgentState(
                task=task,
                repo=repo,
                branch=branch,
                repo_map=repo_map,
            )

            # Generate plan
            plan = await agent._create_plan(state)

            return {
                "success": True,
                "task": task,
                "repo": repo,
                "branch": branch,
                "plan": plan,
                "message": f"**Implementation Plan for:** {task}\n\n{plan}"
            }

        finally:
            await sandbox_manager.destroy(sandbox.id)

    except Exception as e:
        return {"error": f"Planning failed: {e}"}

    finally:
        await agent.close()


async def _handle_task(
    task: str,
    repo: str,
    branch: str,
    create_pr: bool,
    max_fix_attempts: int,
) -> dict:
    """Handle full task execution."""
    if not task:
        return {"error": "Task description is required"}

    # Generate task ID
    import uuid
    task_id = str(uuid.uuid4())[:8]

    # Track the task
    _running_tasks[task_id] = {
        "task": task,
        "repo": repo,
        "branch": branch,
        "phase": "starting",
        "started_at": datetime.utcnow(),
        "messages": [],
    }

    try:
        # Run the agent
        result = await code_agent.run(
            task=task,
            repo=repo,
            branch=branch,
            create_pr=create_pr,
            max_fix_attempts=max_fix_attempts,
            require_plan_approval=True,
            require_code_approval=True,
        )

        # Update tracking
        _running_tasks[task_id]["phase"] = result.phase.value
        _running_tasks[task_id]["messages"] = result.messages

        # Format response
        return _format_result(result, task_id)

    except Exception as e:
        _running_tasks[task_id]["phase"] = "failed"
        _running_tasks[task_id]["messages"].append(f"Error: {e}")
        return {"error": str(e), "task_id": task_id}

    finally:
        # Clean up after a delay
        asyncio.create_task(_cleanup_task(task_id, delay=300))


async def _handle_interactive_task(
    task: str,
    repo: str,
    branch: str,
    create_pr: bool,
    max_fix_attempts: int,
    channel: discord.TextChannel,
    bot: discord.Client,
    user_id: int,
    user_name: str,
    human_review: bool = True,
) -> dict:
    """
    Handle task with interactive Discord updates and human-in-the-loop.

    This provides live progress updates in Discord and allows users
    to reply to messages to provide feedback at review checkpoints.
    """
    if not task:
        return {"error": "Task description is required"}

    import uuid
    task_id = str(uuid.uuid4())[:8]

    # Create progress reporter
    reporter = DiscordProgressReporter(
        channel=channel,
        bot=bot,
        user_id=user_id,
        user_name=user_name,
    )

    # Register for reply routing
    register_reporter(channel.id, reporter)
    _interactive_sessions[channel.id] = {
        "task_id": task_id,
        "reporter": reporter,
        "started_at": datetime.utcnow(),
    }

    # Track the task
    _running_tasks[task_id] = {
        "task": task,
        "repo": repo,
        "branch": branch,
        "phase": "starting",
        "started_at": datetime.utcnow(),
        "messages": [],
        "interactive": True,
        "channel_id": channel.id,
    }

    try:
        # Start task in Discord
        await reporter.start_task(task, repo, task_id)

        # Define progress callback
        async def on_progress(phase: str, message: str, detail: Optional[str] = None):
            """Called by agent on progress updates."""
            _running_tasks[task_id]["phase"] = phase
            _running_tasks[task_id]["messages"].append(message)

            # Update Discord
            await reporter.update_phase(phase, detail)

            # Send detail as separate message for important phases
            if detail and phase in ("planning", "coding", "failed"):
                # Truncate if needed
                if len(detail) > 1800:
                    detail = detail[:1800] + "\n\n*[truncated...]*"
                await reporter.send_detail(f"```\n{detail}\n```")

        # Define approval callback for human review
        async def on_approval_needed(phase: str, content: str) -> tuple[str, str]:
            """Called by agent when human approval is needed."""
            feedback = await reporter.request_approval(
                phase=phase,
                content=content,
                timeout=300.0,  # 5 minute timeout
            )

            if feedback.type == HumanFeedbackType.APPROVE:
                return ("approve", "")
            elif feedback.type == HumanFeedbackType.REJECT:
                return ("reject", feedback.message)
            elif feedback.type == HumanFeedbackType.CANCEL:
                return ("reject", "Cancelled by user")
            else:
                # MODIFY - return the feedback message for revision
                return ("modify", feedback.message)

        # Run the agent with callbacks
        result = await code_agent.run(
            task=task,
            repo=repo,
            branch=branch,
            create_pr=create_pr,
            max_fix_attempts=max_fix_attempts,
            require_plan_approval=True,
            require_code_approval=True,
            on_progress=on_progress,
            on_approval_needed=on_approval_needed,
            use_human_review=human_review,
        )

        # Update tracking
        _running_tasks[task_id]["phase"] = result.phase.value
        _running_tasks[task_id]["messages"] = result.messages

        # Send completion message
        summary = _format_result_summary(result)
        await reporter.complete(result.success, summary)

        return _format_result(result, task_id)

    except Exception as e:
        _running_tasks[task_id]["phase"] = "failed"
        _running_tasks[task_id]["messages"].append(f"Error: {e}")
        await reporter.complete(False, f"Error: {e}")
        return {"error": str(e), "task_id": task_id}

    finally:
        # Clean up
        unregister_reporter(channel.id)
        _interactive_sessions.pop(channel.id, None)
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
    """List all branches in a repository using GraphQL (faster + richer data)."""
    from ...github_graphql import github_graphql

    # Temporarily switch repo context if needed
    original_owner, original_repo = github_graphql.owner, github_graphql.repo
    parts = repo.split("/")
    if len(parts) == 2:
        github_graphql.owner, github_graphql.repo = parts[0], parts[1]

    try:
        data = await github_graphql._fetch_branches(limit=100)

        if data.get("error"):
            return {"error": f"Failed to list branches: {data['error']}"}

        branches = data.get("items", [])
        default_branch = data.get("default", "main")
        branch_list = [b["name"] for b in branches]

        # Format with default branch indicator
        lines = []
        for b in branches[:20]:
            marker = " (default)" if b.get("is_default") else ""
            date = b.get("last_commit", "")
            lines.append(f"- {b['name']}{marker} ({date})")

        return {
            "success": True,
            "branches": branch_list,
            "default_branch": default_branch,
            "message": f"**Branches in {repo}:**\n" + "\n".join(lines)
        }
    finally:
        # Restore original context
        github_graphql.owner, github_graphql.repo = original_owner, original_repo


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


def _format_result_summary(result: AgentResult) -> str:
    """Format a summary for Discord completion message."""
    if result.success:
        parts = [f"**Task:** {result.task[:100]}"]

        if result.changes:
            successful = [c for c in result.changes if c.get('success')]
            parts.append(f"**Files Changed:** {len(successful)}")

        if result.commit_sha:
            parts.append(f"**Commit:** `{result.commit_sha}`")

        if result.pr_url:
            parts.append(f"**PR:** [View](<{result.pr_url}>)")

        parts.append(f"**Duration:** {result.duration:.1f}s")
        return "\n".join(parts)
    else:
        return f"**Error:** {result.error or 'Unknown error'}"


async def _cleanup_task(task_id: str, delay: int):
    """Clean up task tracking after delay."""
    await asyncio.sleep(delay)
    _running_tasks.pop(task_id, None)


def _format_result(result: AgentResult, task_id: str) -> dict:
    """Format agent result for Discord display."""
    if result.success:
        # Success message
        msg_parts = [
            f"✅ **Task Completed Successfully!**",
            f"",
            f"**Task:** {result.task}",
            f"**Repository:** {result.repo} ({result.branch})",
            f"**Duration:** {result.duration:.1f}s",
        ]

        if result.changes:
            changes_list = [f"  - {c['file']}" for c in result.changes if c.get('success')]
            if changes_list:
                msg_parts.append(f"")
                msg_parts.append(f"**Files Changed:**")
                msg_parts.extend(changes_list[:10])
                if len(changes_list) > 10:
                    msg_parts.append(f"  ... and {len(changes_list) - 10} more")

        if result.commit_sha:
            msg_parts.append(f"")
            msg_parts.append(f"**Commit:** `{result.commit_sha}`")

        if result.pr_url:
            msg_parts.append(f"**Pull Request:** [View PR](<{result.pr_url}>)")

        return {
            "success": True,
            "task_id": task_id,
            "message": "\n".join(msg_parts),
            "commit_sha": result.commit_sha,
            "pr_url": result.pr_url,
            "changes": result.changes,
            "duration": result.duration,
        }

    else:
        # Failure message
        msg_parts = [
            f"❌ **Task Failed**",
            f"",
            f"**Task:** {result.task}",
            f"**Phase:** {result.phase.value}",
            f"**Error:** {result.error or 'Unknown error'}",
        ]

        if result.messages:
            msg_parts.append(f"")
            msg_parts.append(f"**Progress Log:**")
            for msg in result.messages[-5:]:
                msg_parts.append(f"  {msg}")

        return {
            "success": False,
            "task_id": task_id,
            "error": result.error,
            "phase": result.phase.value,
            "message": "\n".join(msg_parts),
        }


# Export tool handler
TOOL_HANDLERS = {
    "github_code": tool_github_code,
}
