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
import json
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

import discord

from ..sandbox import sandbox_manager, Sandbox, SANDBOX_DIR, get_persistent_sandbox
from ..claude_code_agent import get_claude_code_agent, ClaudeCodeResult, parse_todos_from_output, TodoItem
from ..embed_builder import ProgressEmbedManager, StepStatus

logger = logging.getLogger(__name__)

# Task persistence file
TASKS_FILE = SANDBOX_DIR / "tasks.json"

# Store running tasks for status checks (loaded from disk on import)
_running_tasks: dict[str, dict] = {}

# NOTE: thread_to_task mapping removed - thread_id IS the task_id now
# No mapping needed: thread_id = task_id = branch name = ccr session ID


def _save_tasks():
    """Save tasks to disk for persistence across restarts."""
    try:
        # Only save serializable fields (no embed_manager, etc.)
        serializable = {}
        for task_id, task_data in _running_tasks.items():
            serializable[task_id] = {
                "task_id": task_id,
                "task": task_data.get("task", ""),
                "repo": task_data.get("repo", "pollinations/pollinations"),
                "branch": task_data.get("branch", "main"),
                "branch_name": task_data.get("branch_name"),
                "phase": task_data.get("phase", "unknown"),
                "started_at": task_data.get("started_at").isoformat() if isinstance(task_data.get("started_at"), datetime) else task_data.get("started_at"),
                "files_changed": task_data.get("files_changed", []),
                "user": task_data.get("user"),
                "channel_id": task_data.get("channel_id"),
                "thread_id": task_data.get("thread_id"),
            }

        # Simplified: just save tasks, no mapping needed
        # thread_id IS the task_id now
        save_data = {"tasks": serializable}

        TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TASKS_FILE.write_text(json.dumps(save_data, indent=2))
        logger.debug(f"Saved {len(serializable)} tasks to {TASKS_FILE}")
    except Exception as e:
        logger.warning(f"Failed to save tasks: {e}")


def _load_tasks():
    """Load tasks from disk on startup."""
    global _running_tasks
    try:
        if TASKS_FILE.exists():
            raw_data = json.loads(TASKS_FILE.read_text())

            # Handle both old format (flat tasks) and new format (tasks wrapper)
            if "tasks" in raw_data:
                data = raw_data["tasks"]
            else:
                # Old format - just tasks
                data = raw_data

            for task_id, task_data in data.items():
                # Convert ISO string back to datetime
                if task_data.get("started_at"):
                    try:
                        task_data["started_at"] = datetime.fromisoformat(task_data["started_at"])
                    except:
                        task_data["started_at"] = datetime.utcnow()
                _running_tasks[task_id] = task_data

            logger.info(f"Loaded {len(_running_tasks)} tasks")
    except Exception as e:
        logger.warning(f"Failed to load tasks: {e}")


# Load tasks on module import
_load_tasks()


# NOTE: get_task_for_thread and set_task_for_thread removed
# With thread_id as universal key, no mapping is needed:
# - thread_id IS the task_id
# - Just use str(thread_id) to look up in _running_tasks


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
    # Branch naming for push/PR (converts task/* to proper names like feat/*, fix/*)
    branch_type: Optional[str] = None,  # feat, fix, docs, refactor, chore, etc.
    branch_description: Optional[str] = None,  # Short description for branch name
    # Discord context (injected by bot.py)
    discord_channel: Optional[discord.TextChannel] = None,
    discord_thread_id: Optional[int] = None,  # Thread ID for automatic task_id lookup
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

    # SIMPLIFIED: thread_id IS the task_id now - no lookup needed!
    # If we have a thread_id, that's our task_id
    if discord_thread_id and not task_id:
        task_id = str(discord_thread_id)
        logger.info(f"Using thread_id as task_id: {task_id}")

    try:
        # Status check (no sandbox needed)
        if action == "status":
            return await _handle_status(task_id)

        # List all tasks (useful after restart to see available tasks)
        if action == "list_tasks":
            return _handle_list_tasks()

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
                existing_task_id=task_id,  # Reuse existing branch if task_id provided
                thread_id=discord_thread_id,  # For thread→task mapping
                discord_user_id=kwargs.get("discord_user_id", 0),  # For terminal ownership
            )

        # Sandbox operations - work with existing sandbox
        if action == "run_in_sandbox":
            return await _handle_run_in_sandbox(kwargs.get("sandbox_id"), kwargs.get("command"))

        if action == "read_sandbox_file":
            return await _handle_read_sandbox_file(kwargs.get("sandbox_id"), file_path)

        if action == "write_sandbox_file":
            return await _handle_write_sandbox_file(kwargs.get("sandbox_id"), file_path, file_content)

        if action == "destroy_sandbox":
            return await _handle_destroy_sandbox(kwargs.get("sandbox_id"), kwargs.get("task_id"), discord_user_name)

        if action == "update_embed":
            return await _handle_update_embed(kwargs.get("task_id"), kwargs.get("status"), kwargs.get("finish", False))

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
            return await _handle_push(repo, branch, task_id, branch_type, branch_description)

        if action == "open_pr":
            return await _handle_open_pr(repo, branch, base_branch, pr_title, pr_body, task_id, branch_type, branch_description)

        return {"error": f"Unknown action: {action}. Available: task, status, list_tasks, update_embed, run_in_sandbox, read_sandbox_file, write_sandbox_file, destroy_sandbox, list_branches, create_branch, delete_branch, read_file, list_files, edit_file, commit, push, open_pr"}

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


def _handle_list_tasks() -> dict:
    """
    List all persisted tasks.

    Useful after bot restart to see what tasks exist and can be resumed.
    Bot AI can use task_id from this list for push/open_pr operations.
    """
    if not _running_tasks:
        return {
            "message": "No tasks found. Start a new task with action='task'.",
            "tasks": []
        }

    tasks_list = []
    message_lines = ["**Available Tasks:**"]

    for task_id, info in _running_tasks.items():
        task_summary = {
            "task_id": task_id,
            "task": info.get("task", "")[:100],
            "branch_name": info.get("branch_name"),
            "phase": info.get("phase", "unknown"),
            "files_changed": info.get("files_changed", []),
            "user": info.get("user"),
            "repo": info.get("repo", "pollinations/pollinations"),
        }
        tasks_list.append(task_summary)

        # Format for display
        branch = info.get("branch_name", "unknown")
        phase = info.get("phase", "unknown")
        files = len(info.get("files_changed", []))
        message_lines.append(
            f"- **{task_id}** ({phase}): {info.get('task', '')[:50]}...\n"
            f"  Branch: `{branch}` | Files changed: {files}"
        )

    return {
        "message": "\n".join(message_lines),
        "tasks": tasks_list,
        "_ai_hint": (
            "Task IDs are now Discord thread IDs - the universal key.\n"
            "When user is in a thread, thread_id is auto-injected so you don't need task_id.\n\n"
            "For PR/push from a thread (most common case):\n"
            "- polly_agent(action='open_pr', pr_title='...', pr_body='...',\n"
            "             branch_type='feat|fix|docs', branch_description='short-description')\n\n"
            "IMPORTANT: When pushing or creating PRs, use branch_type and branch_description\n"
            "to give branches proper names like feat/xyz, fix/abc instead of thread/12345."
        )
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


async def _handle_destroy_sandbox(sandbox_id: Optional[str], task_id: Optional[str], user_name: Optional[str]) -> dict:
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

    # Finish the embed if we have a task_id
    if task_id and task_id in _running_tasks:
        embed_manager = _running_tasks[task_id].get("embed_manager")
        if embed_manager:
            embed_manager.set_status("Sandbox destroyed")
            await embed_manager.finish(success=True)

    await sandbox_manager.destroy(sandbox_id, force=True)

    return {
        "success": True,
        "sandbox_id": sandbox_id,
        "message": f"Sandbox {sandbox_id} has been destroyed.",
    }


async def _handle_update_embed(task_id: Optional[str], status: Optional[str], finish: bool = False) -> dict:
    """Update the Discord embed for a task. Bot AI can call this to show progress."""
    if not task_id:
        return {"error": "task_id is required"}

    if task_id not in _running_tasks:
        return {"error": f"Task {task_id} not found"}

    embed_manager = _running_tasks[task_id].get("embed_manager")
    if not embed_manager:
        return {"error": f"No embed manager for task {task_id}"}

    if status:
        embed_manager.set_status(status)

    if finish:
        await embed_manager.finish(success=True)
    else:
        await embed_manager.update()

    return {
        "success": True,
        "task_id": task_id,
        "status": status,
        "finished": finish,
    }


async def _handle_code_task(
    task: str,
    repo: str,
    branch: str,
    channel: Optional[discord.TextChannel] = None,
    user_name: Optional[str] = None,
    existing_task_id: Optional[str] = None,  # Legacy - kept for compatibility
    thread_id: Optional[int] = None,  # Discord thread ID - THE universal key
    discord_user_id: int = 0,  # Discord user ID for terminal ownership
) -> dict:
    """
    Handle coding task via ccr.

    This architecture:
    - Uses ClaudeCodeAgent which manages the persistent sandbox
    - ClaudeCodeAgent creates task branch internally (or reuses existing)
    - Runs the task prompt via ccr
    - Returns results with sandbox still running for follow-ups

    The bot AI handles:
    - Building task context
    - Summarizing output for Discord
    - Managing user interactions (pause, resume, etc.)

    Thread ID as Universal Key:
    - thread_id = task_id = branch name = ccr session ID
    - No mapping needed - if you have thread_id, you have everything
    - Subsequent calls with same thread_id automatically continue on same branch
    """
    if not task:
        return {"error": "Task description is required"}

    # SIMPLIFIED: Use thread_id as the universal key
    # thread_id = task_id = branch = ccr session
    if thread_id:
        task_id = str(thread_id)
    elif existing_task_id:
        task_id = existing_task_id
    else:
        # Fallback for non-Discord usage
        import uuid
        task_id = str(uuid.uuid4())[:8]

    # Check if task already exists (continuing work in same thread)
    is_continuation = task_id in _running_tasks

    # Track the task (update if existing, create if new)
    if not is_continuation:
        _running_tasks[task_id] = {
            "task": task,
            "repo": repo,
            "branch": branch,
            "phase": "coding",
            "started_at": datetime.utcnow(),
            "messages": [],
            "user": user_name,
            "thread_id": thread_id,
        }
        logger.info(f"New task {task_id} (thread_id={thread_id})")
    else:
        # Update existing task with new task description
        _running_tasks[task_id]["task"] = task
        _running_tasks[task_id]["phase"] = "coding"
        _running_tasks[task_id]["messages"].append(f"Continuing with: {task[:50]}...")
        logger.info(f"Continuing task {task_id} (thread_id={thread_id})")

    # Create progress embed if Discord channel available (Claude Code style)
    embed_manager: Optional[ProgressEmbedManager] = None
    dynamic_todo_indices: dict[str, int] = {}  # Track todo content -> step index
    if channel:
        embed_manager = ProgressEmbedManager(channel)
        try:
            await embed_manager.start(current_action="Setting up environment")
            await embed_manager.update()
        except Exception as e:
            logger.warning(f"Failed to create progress embed: {e}")
            embed_manager = None

    try:
        _running_tasks[task_id]["messages"].append(f"Task {task_id} starting")

        # Get the persistent sandbox
        sandbox = get_persistent_sandbox()

        # Ensure sandbox is running
        if not await sandbox.ensure_running():
            return {"error": "Failed to start sandbox container"}

        # Get/create terminal for this thread FIRST
        if embed_manager:
            embed_manager.set_action("Creating terminal")
            await embed_manager.update()

        discord_channel_id = channel.id if channel else 0
        terminal = await sandbox.terminal_manager.get_terminal(
            task_id,
            user_id=discord_user_id,
            channel_id=discord_channel_id,
        )
        logger.info(f"Terminal ready for task {task_id}")

        # Create/checkout task branch
        if embed_manager:
            embed_manager.set_action("Setting up branch")
            await embed_manager.update()

        branch_name = f"thread/{task_id}"

        # Fetch latest and create branch from origin/main
        await terminal.send_command("git fetch origin", timeout=60)

        # Check if branch exists
        branch_check = await terminal.send_command(f"git branch --list {branch_name}", timeout=10)
        if branch_name in branch_check:
            # Branch exists, checkout and rebase
            await terminal.send_command(f"git checkout {branch_name}", timeout=30)
            logger.info(f"Checked out existing branch {branch_name}")
        else:
            # Create new branch from origin/main
            await terminal.send_command(f"git checkout -b {branch_name} origin/main", timeout=30)
            logger.info(f"Created new branch {branch_name}")

        # Build ccr command
        full_prompt = (
            f"IMPORTANT: You are working on branch '{branch_name}'. "
            "Commit your changes to THIS branch. "
            "Do NOT include any Claude, AI, or bot attribution in commit messages. "
            "Just describe what was changed.\n\n"
            f"{task}"
        )
        escaped_prompt = shlex.quote(full_prompt)
        ccr_cmd = f"ccr code -p --dangerously-skip-permissions {escaped_prompt}"

        # Run ccr in terminal
        if embed_manager:
            embed_manager.set_action("Running ccr")
            await embed_manager.update()

        logger.info(f"Running ccr: {task[:100]}...")
        start_time = asyncio.get_running_loop().time()

        output = await terminal.send_command(ccr_cmd, timeout=None)  # No timeout for ccr

        duration = asyncio.get_running_loop().time() - start_time
        logger.info(f"ccr completed in {duration:.1f}s, output {len(output)} bytes")

        # Parse results from output
        todos = parse_todos_from_output(output)

        # Get files changed
        diff_result = await terminal.send_command("git diff --name-only HEAD~1 2>/dev/null || echo ''", timeout=30)
        files_changed = [f.strip() for f in diff_result.split('\n') if f.strip() and not f.startswith('fatal')]

        # Build result object (compatible with ClaudeCodeResult)
        class SimpleResult:
            def __init__(self):
                self.success = True
                self.output = output
                self.branch_name = branch_name
                self.files_changed = files_changed
                self.todos = todos
                self.duration_seconds = int(duration)
                self.error = None

        result = SimpleResult()

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

            # Final action message
            if result.success:
                files_msg = f"{len(result.files_changed)} file(s) changed" if result.files_changed else "No changes"
                embed_manager.set_action(files_msg)
            else:
                embed_manager.set_action(result.error[:50] if result.error else "Task failed")

            await embed_manager.update()
            # Store embed_manager for later updates (e.g., when PR is created)
            _running_tasks[task_id]["embed_manager"] = embed_manager

        # Update tracking
        _running_tasks[task_id]["phase"] = "complete" if result.success else "failed"
        _running_tasks[task_id]["messages"].append(f"Duration: {result.duration_seconds}s")
        _running_tasks[task_id]["branch_name"] = result.branch_name
        _running_tasks[task_id]["files_changed"] = result.files_changed
        _running_tasks[task_id]["user"] = user_name

        # Save to disk for persistence across restarts
        _save_tasks()

        # Include todos in response
        todos_summary = [{"content": t.content, "status": t.status} for t in result.todos]

        # Return FULL ccr output - let bot AI read it and decide what to do
        # This enables dynamic conversation: AI can reply to ccr, ask user, or mark done
        return {
            "success": result.success,
            "task_id": task_id,
            "task": task,
            "repo": repo,
            "branch": result.branch_name or branch,
            "ccr_response": result.output,  # FULL ccr output - AI reads this and decides
            "files_changed": result.files_changed,
            "todos": todos_summary,
            "duration": result.duration_seconds,
            "error": result.error,
            "_ai_hint": (
                "⚠️ CRITICAL: Your Discord response MUST be based ONLY on ccr_response above!\n"
                "ccr_response contains what the coding agent ACTUALLY did - READ IT.\n\n"
                "❌ NEVER say: 'I cannot access', 'I don't have ability', 'Let me help you manually'\n"
                "✅ ALWAYS say: 'ccr [did X]' based on the actual ccr_response content\n\n"
                "DECISION TREE:\n"
                "1. ccr SUCCESS + files_changed → Summarize changes FROM ccr_response, ask 'Create a PR?'\n"
                "2. ccr SUCCESS + no files → Report what ccr found/said FROM ccr_response\n"
                "3. ccr NEEDS INFO → Use YOUR tools (code_search, github_issue) to get it, call polly_agent again\n"
                "4. ccr ERROR → Explain the ACTUAL error FROM ccr_response\n\n"
                "TASK IS NOT DONE until user confirms.\n\n"
                "🔑 SIMPLIFIED: Thread ID is the universal key!\n"
                "- All follow-ups from this Discord thread AUTOMATICALLY use the same git branch\n"
                "- No need to pass task_id - it's derived from thread_id automatically\n"
                "- Git branch IS the state - all previous changes are committed and visible\n"
                "- Terminal persists per thread - ccr keeps conversation context across calls!\n"
                "- If ccr auto-compacts (long session), new session inherits context in same terminal\n\n"
                "TO CREATE PR (when user confirms) - USE PROPER BRANCH NAMING:\n"
                "polly_agent(action='open_pr', pr_title='...', pr_body='...',\n"
                "           branch_type='feat|fix|docs|refactor|chore', branch_description='short-description')\n"
                "Branch types: feat (new feature), fix (bug fix), docs (documentation),\n"
                "              refactor (code refactor), chore (maintenance), test, style, perf, ci\n"
                "Example: branch_type='feat', branch_description='add dark mode toggle'\n"
                "         → creates branch: feat/add-dark-mode-toggle\n\n"
                "TO CONTINUE WORK (follow-up task) - just call polly_agent again:\n"
                "polly_agent(action='task', task='also add tests')\n"
                "The thread_id is auto-injected, so ccr continues on the same branch with full context."
            )
        }

    except Exception as e:
        _running_tasks[task_id]["phase"] = "failed"
        _running_tasks[task_id]["messages"].append(f"Error: {e}")
        _save_tasks()  # Persist error state too
        logger.exception("Task failed")

        if embed_manager:
            embed_manager.set_status(f"Error: {e}")
            await embed_manager.finish(success=False)

        return {
            "success": False,
            "error": str(e),
            "task_id": task_id,
            "task": task,  # Include original task for context
            "repo": repo,
            "branch": branch,
            "_ai_hint": (
                f"⚠️ Task failed with error: {e}\n\n"
                "DO NOT say 'I cannot access' - explain the ACTUAL error above.\n"
                "Options:\n"
                "1. Retry with simpler task description\n"
                "2. Use code_search/github_issue to gather more context, then retry\n"
                "3. Explain the error to user and ask how to proceed\n\n"
                "You have all the context - don't ask user for info you already have."
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


def _generate_branch_name(
    branch_type: Optional[str],
    branch_description: Optional[str],
    task_id: Optional[str] = None
) -> Optional[str]:
    """
    Generate a proper branch name from type and description.

    Examples:
        branch_type="feat", branch_description="add dark mode" -> "feat/add-dark-mode"
        branch_type="fix", branch_description="null pointer bug" -> "fix/null-pointer-bug"

    Returns None if branch_type is not provided (keeps original branch name).
    """
    if not branch_type:
        return None

    # Normalize type
    valid_types = ["feat", "fix", "docs", "refactor", "chore", "test", "style", "perf", "ci"]
    branch_type = branch_type.lower().strip()
    if branch_type not in valid_types:
        logger.warning(f"Unknown branch type '{branch_type}', using as-is")

    # Generate description slug
    if branch_description:
        # Convert to slug: lowercase, replace spaces with dashes, remove special chars
        import re
        slug = branch_description.lower().strip()
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)  # Remove special chars
        slug = re.sub(r'\s+', '-', slug)  # Spaces to dashes
        slug = re.sub(r'-+', '-', slug)  # Multiple dashes to single
        slug = slug.strip('-')[:50]  # Limit length
    else:
        # Use task_id if no description
        slug = task_id or "update"

    return f"{branch_type}/{slug}"


async def _handle_push(
    repo: str,
    branch: str,
    task_id: Optional[str] = None,
    branch_type: Optional[str] = None,
    branch_description: Optional[str] = None
) -> dict:
    """
    Push the sandbox branch to GitHub using GitHub App (Polly Bot) credentials.

    ccr works in the Docker sandbox - changes are committed there.
    This function pushes those commits to GitHub using the App's credentials.

    The push shows as "Polly Bot" and uses secure App token authentication.

    Branch naming:
    - If branch_type is provided, renames task/* branch to proper name (e.g., feat/*, fix/*)
    - This rename happens locally before push, so GitHub sees the proper branch name
    """
    from ..sandbox import get_persistent_sandbox

    sandbox = get_persistent_sandbox()

    # Check if sandbox is running
    if not await sandbox.is_running():
        return {"error": "Sandbox is not running. Cannot push changes."}

    # Get the branch name - either from task tracking or parameter
    actual_branch = branch
    if task_id and task_id in _running_tasks:
        actual_branch = _running_tasks[task_id].get("branch_name", branch)

    # Generate proper branch name if type provided
    target_branch = actual_branch
    proper_name = _generate_branch_name(branch_type, branch_description, task_id)

    if proper_name and (actual_branch.startswith("task/") or actual_branch.startswith("thread/")):
        # Rename branch locally before pushing (task/* or thread/* → feat/*, fix/*, etc.)
        logger.info(f"Renaming branch {actual_branch} → {proper_name}")

        rename_result = await sandbox.execute(
            f"cd /workspace/pollinations && git branch -m {actual_branch} {proper_name}",
            as_coder=True
        )

        if rename_result.exit_code == 0:
            target_branch = proper_name
            # Update task tracking with new branch name
            if task_id and task_id in _running_tasks:
                _running_tasks[task_id]["branch_name"] = proper_name
                _save_tasks()
            logger.info(f"Branch renamed to {proper_name}")
        else:
            logger.warning(f"Failed to rename branch: {rename_result.stderr}, using original name")

    logger.info(f"Pushing branch {target_branch} to origin using GitHub App...")

    # Use the sandbox's push_branch method which handles App credentials
    push_result = await sandbox.push_branch(target_branch, repo)

    if push_result.exit_code == 0:
        return {
            "success": True,
            "branch": target_branch,
            "original_branch": actual_branch if actual_branch != target_branch else None,
            "message": f"✅ Pushed branch `{target_branch}` to GitHub (via Polly Bot)"
        }
    else:
        error_msg = push_result.stderr or push_result.stdout
        # Check for common errors
        if "rejected" in error_msg.lower():
            return {"error": f"Push rejected - branch may have diverged. Error: {error_msg[:200]}"}
        if "credential" in error_msg.lower() or "authentication" in error_msg.lower():
            return {"error": f"GitHub authentication failed. Check if Polly Bot is installed on the repo. Error: {error_msg[:200]}"}
        return {"error": f"Failed to push: {error_msg[:300]}"}


async def _handle_open_pr(
    repo: str,
    head_branch: str,
    base_branch: Optional[str],
    title: Optional[str],
    body: Optional[str],
    task_id: Optional[str] = None,
    branch_type: Optional[str] = None,
    branch_description: Optional[str] = None
) -> dict:
    """
    Create a pull request.

    This first pushes the sandbox branch to GitHub (if not already pushed),
    then creates the PR via GitHub API.

    Branch naming:
    - If branch_type provided, renames task/* to proper name before pushing
    - Example: branch_type="feat", branch_description="dark mode" -> feat/dark-mode
    """
    if not title:
        return {"error": "pr_title parameter is required"}

    base = base_branch or "main"

    # Get actual branch name from task tracking if available
    actual_branch = head_branch
    if task_id and task_id in _running_tasks:
        actual_branch = _running_tasks[task_id].get("branch_name", head_branch)

    # First, push the branch to GitHub (this handles renaming if branch_type provided)
    logger.info(f"Pushing branch {actual_branch} before creating PR...")
    push_result = await _handle_push(repo, actual_branch, task_id, branch_type, branch_description)

    if not push_result.get("success"):
        return {"error": f"Failed to push branch before PR: {push_result.get('error', 'Unknown error')}"}

    # Now create the PR via GitHub API
    pr_data = {
        "title": title,
        "head": actual_branch,
        "base": base,
        "body": body or f"Created by Polli bot.\n\n🤖 Automated PR"
    }

    status, data = await _github_api("POST", f"/repos/{repo}/pulls", pr_data)

    if status == 201:
        # Update embed if we have task tracking
        if task_id and task_id in _running_tasks:
            embed_manager = _running_tasks[task_id].get("embed_manager")
            if embed_manager:
                embed_manager.set_status(f"PR #{data['number']} created!")
                await embed_manager.finish(success=True)

        return {
            "success": True,
            "pr_number": data["number"],
            "pr_url": data["html_url"],
            "branch": actual_branch,
            "message": f"✅ Created PR #{data['number']}: [{title}](<{data['html_url']}>)"
        }
    elif status == 422 and "pull request already exists" in str(data).lower():
        # PR already exists - find it
        list_status, list_data = await _github_api(
            "GET",
            f"/repos/{repo}/pulls?head={repo.split('/')[0]}:{actual_branch}&state=open"
        )
        if list_status == 200 and list_data:
            existing_pr = list_data[0]
            return {
                "success": True,
                "pr_number": existing_pr["number"],
                "pr_url": existing_pr["html_url"],
                "message": f"ℹ️ PR already exists: #{existing_pr['number']}: [{existing_pr['title']}](<{existing_pr['html_url']}>)"
            }
        return {"error": f"PR already exists but couldn't find it: {data.get('message', status)}"}
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
