"""
Autonomous Agent - AI decides everything dynamically.

Philosophy: Give the AI raw capabilities (shell, web, user interaction) and let it
figure out how to accomplish tasks - just like a human developer would.

No predefined workflows. No forced phases. No artificial tool constraints.
The AI has a terminal and decides what commands to run, when to search the web,
and when to ask the user for help.

This is as close to "human developer" as we can get.
"""

import asyncio
import json
import logging
import shlex
import re
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from datetime import datetime

from .models import ModelRouter, model_router
from .sandbox import SandboxManager, sandbox_manager, Sandbox

logger = logging.getLogger(__name__)

# Callback types
ProgressCallback = Callable[[str, str, Optional[str]], Awaitable[None]]


# =============================================================================
# Tools - AI's capabilities (AI decides WHEN and HOW to use them)
# =============================================================================

TOOLS = [
    # File operations
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use to understand existing code before making changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file relative to repo root (e.g., 'src/main.py')"
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or completely replace a file. For partial edits, prefer edit_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to write"
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete file content to write"
                    }
                },
                "required": ["file_path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file by replacing a specific section. More precise than write_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to edit"
                    },
                    "old_content": {
                        "type": "string",
                        "description": "The exact content to find and replace (must be unique in the file)"
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The new content to replace it with"
                    }
                },
                "required": ["file_path", "old_content", "new_content"]
            }
        }
    },
    # Search & explore
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for code patterns using grep/regex. Returns matching lines with file paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (regex supported)"
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional file glob to filter (e.g., '*.py', 'src/**/*.js')"
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "Semantic code search using embeddings. Finds conceptually related code even without exact matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query (e.g., 'function that handles user authentication')"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default: 10)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory or matching a pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: repo root)"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter (e.g., '*.py')"
                    }
                },
                "required": []
            }
        }
    },
    # Execution
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run any shell command. Use for: tests, builds, linting, installing deps, scripts, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (e.g., 'pytest -v', 'npm test', 'pip install x')"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 120, max: 600)"
                    }
                },
                "required": ["command"]
            }
        }
    },
    # Web & research
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for info - docs, error solutions, best practices, APIs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "deep": {
                        "type": "boolean",
                        "description": "Use reasoning model for complex research (default: false)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    # Git operations
    {
        "type": "function",
        "function": {
            "name": "commit",
            "description": "Stage all changes and commit with a message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message describing the changes"
                    }
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_branch",
            "description": "Create and switch to a new git branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch_name": {
                        "type": "string",
                        "description": "Name for the new branch"
                    }
                },
                "required": ["branch_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_pr",
            "description": "Create a pull request with the current branch changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "PR title"
                    },
                    "body": {
                        "type": "string",
                        "description": "PR description"
                    },
                    "base": {
                        "type": "string",
                        "description": "Base branch to merge into (default: main)"
                    }
                },
                "required": ["title", "body"]
            }
        }
    },
    # User interaction
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user for clarification, approval, or input when needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Question to ask"
                    }
                },
                "required": ["question"]
            }
        }
    },
    # Completion
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal task completion. Call when finished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Summary of what was accomplished"
                    },
                    "files_changed": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of files that were modified"
                    }
                },
                "required": ["summary"]
            }
        }
    },
]


# System prompt - AI is 100% in control, no forced workflow
SYSTEM_PROMPT = """You are an autonomous AI developer. You decide how to approach tasks.

The repo is pre-loaded and ready. Embeddings are available for semantic search.

You have full control:
- Decide your own approach based on the task
- Use tools in any order that makes sense
- Ask the user if you need clarification
- Create branches/PRs when appropriate

No forced workflow. No required phases. Just accomplish the task like a skilled developer would.

Call done() when complete."""


@dataclass
class AutonomousResult:
    """Result of autonomous agent execution."""
    success: bool
    summary: str
    files_changed: list[str] = field(default_factory=list)
    sandbox_id: Optional[str] = None
    commit_sha: Optional[str] = None
    error: Optional[str] = None
    tool_calls: int = 0
    duration: float = 0.0
    conversation: list[dict] = field(default_factory=list)


class AutonomousAgent:
    """
    Truly autonomous coding agent.

    The AI has raw shell access and decides everything:
    - What to read/write/search/run
    - When to test
    - When to commit
    - What approach to take

    No predefined workflow. No forced phases. Just capability and intelligence.
    """

    def __init__(
        self,
        model_router: ModelRouter = model_router,
        sandbox_manager: SandboxManager = sandbox_manager,
        max_iterations: int = 50,  # More iterations for complex tasks
    ):
        self.models = model_router
        self.sandboxes = sandbox_manager
        self.max_iterations = max_iterations
        self._initialized = False

    async def initialize(self):
        """Initialize required components."""
        if not self._initialized:
            await self.models.initialize()
            await self.sandboxes.start()
            self._initialized = True
            logger.info("AutonomousAgent initialized")

    async def close(self):
        """Clean up resources."""
        await self.models.close()
        await self.sandboxes.stop()
        self._initialized = False

    async def run(
        self,
        task: str,
        repo: str,
        branch: str = "main",
        model: str = "claude-large",
        on_progress: Optional[ProgressCallback] = None,
        initiated_by: Optional[str] = None,
        initiated_source: Optional[str] = None,
        context: Optional[str] = None,
    ) -> AutonomousResult:
        """
        Run the autonomous agent on a task.

        Args:
            task: What to accomplish (natural language)
            repo: Repository (owner/repo format)
            branch: Starting branch
            model: Model to use (claude-large recommended)
            on_progress: Callback for progress updates
            initiated_by: Who started this
            initiated_source: Source platform ("discord" or "github")
            context: Additional context (issue body, PR description, etc.)

        Returns:
            AutonomousResult with outcome and details
        """
        await self.initialize()
        start_time = datetime.utcnow()

        # Create sandbox
        if on_progress:
            await on_progress("setup", f"Creating sandbox for {repo}", None)

        sandbox = await self.sandboxes.create(
            repo_url=f"https://github.com/{repo}.git",
            branch=branch,
            initiated_by=initiated_by,
            initiated_source=initiated_source,
        )

        # Build initial context
        task_message = f"""Repository: {repo}
Branch: {branch}
Working directory: /workspace (the cloned repo)

Task: {task}"""

        if context:
            task_message += f"\n\nAdditional context:\n{context}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task_message},
        ]

        # Track state
        files_changed: list[str] = []
        tool_call_count = 0
        completed = False
        summary = ""
        commit_sha: Optional[str] = None
        error: Optional[str] = None

        try:
            for iteration in range(self.max_iterations):
                if on_progress:
                    await on_progress("thinking", f"Iteration {iteration + 1}", None)

                # Call the model
                response = await self.models.chat(
                    model_id=model,
                    messages=messages,
                    task_type="coding",
                    tools=TOOLS,
                )

                if response.get("error"):
                    error = f"API error: {response['error']}"
                    logger.error(error)
                    break

                content = response.get("content", "")
                tool_calls = response.get("tool_calls", [])

                # Add assistant response
                assistant_message = {"role": "assistant", "content": content}
                if tool_calls:
                    assistant_message["tool_calls"] = tool_calls
                messages.append(assistant_message)

                # No tool calls = AI is done thinking or stuck
                if not tool_calls:
                    if content:
                        summary = content
                    break

                # Execute tools
                for tool_call in tool_calls:
                    tool_call_count += 1
                    tool_name = tool_call.get("function", {}).get("name", "")
                    tool_args_str = tool_call.get("function", {}).get("arguments", "{}")
                    tool_id = tool_call.get("id", f"call_{tool_call_count}")

                    try:
                        tool_args = json.loads(tool_args_str)
                    except json.JSONDecodeError:
                        tool_args = {}

                    if on_progress:
                        preview = tool_args.get("command", tool_args.get("query", ""))[:100]
                        await on_progress("tool", tool_name, preview)

                    # Execute
                    result = await self._execute_tool(
                        sandbox=sandbox,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        files_changed=files_changed,
                    )

                    # Check completion
                    if tool_name == "done":
                        completed = True
                        summary = tool_args.get("summary", "Task completed")
                        break

                    # Track commits
                    if result.get("commit_sha"):
                        commit_sha = result["commit_sha"]

                    # Add result to conversation
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": json.dumps(result, default=str)[:12000],
                    })

                if completed:
                    break

            if not completed and not error:
                error = f"Max iterations ({self.max_iterations}) reached"

        except Exception as e:
            logger.exception("Autonomous agent error")
            error = str(e)

        duration = (datetime.utcnow() - start_time).total_seconds()

        return AutonomousResult(
            success=completed and not error,
            summary=summary,
            files_changed=list(set(files_changed)),
            sandbox_id=sandbox.id,
            commit_sha=commit_sha,
            error=error,
            tool_calls=tool_call_count,
            duration=duration,
            conversation=messages,
        )

    async def _execute_tool(
        self,
        sandbox: Sandbox,
        tool_name: str,
        tool_args: dict,
        files_changed: list[str],
    ) -> dict:
        """Execute a tool and return the result."""
        from .session_embeddings import session_embeddings_manager

        try:
            # === File operations ===
            if tool_name == "read_file":
                file_path = tool_args.get("file_path", "")
                content = await self.sandboxes.read_file(sandbox.id, file_path)
                return {"success": True, "content": content[:15000]}

            elif tool_name == "write_file":
                file_path = tool_args.get("file_path", "")
                content = tool_args.get("content", "")
                await self.sandboxes.write_file(sandbox.id, file_path, content)
                files_changed.append(file_path)
                return {"success": True, "message": f"Wrote {file_path}"}

            elif tool_name == "edit_file":
                file_path = tool_args.get("file_path", "")
                old_content = tool_args.get("old_content", "")
                new_content = tool_args.get("new_content", "")

                current = await self.sandboxes.read_file(sandbox.id, file_path)
                if old_content not in current:
                    return {"success": False, "error": f"Could not find the specified content in {file_path}"}

                updated = current.replace(old_content, new_content, 1)
                await self.sandboxes.write_file(sandbox.id, file_path, updated)
                files_changed.append(file_path)
                return {"success": True, "message": f"Edited {file_path}"}

            # === Search & explore ===
            elif tool_name == "search_code":
                pattern = tool_args.get("pattern", "")
                file_pattern = tool_args.get("file_pattern", "")

                # Sanitize inputs to prevent command injection
                safe_pattern = shlex.quote(pattern)
                cmd = f"grep -rn {safe_pattern} ."
                if file_pattern:
                    safe_file_pattern = shlex.quote(file_pattern)
                    cmd = f"find . -name {safe_file_pattern} -exec grep -l {safe_pattern} {{}} \\;"

                result = await self.sandboxes.execute(sandbox.id, cmd, timeout=30)
                # grep returns exit code 1 for "no matches" which is not an error
                return {
                    "success": result.exit_code in (0, 1),
                    "matches": result.stdout[:5000] if result.stdout else "No matches found",
                }

            elif tool_name == "semantic_search":
                query = tool_args.get("query", "")
                top_k = tool_args.get("top_k", 10)
                results = await session_embeddings_manager.search_combined(sandbox.id, query, top_k)
                return {"success": True, "results": results}

            elif tool_name == "list_files":
                path = tool_args.get("path", ".")
                pattern = tool_args.get("pattern", "*")
                files = await self.sandboxes.list_files(sandbox.id, path, pattern)
                return {"success": True, "files": files[:100]}

            # === Execution ===
            elif tool_name == "run_command":
                command = tool_args.get("command", "")
                timeout = min(tool_args.get("timeout", 120), 600)

                result = await self.sandboxes.execute(sandbox.id, command, timeout=timeout)
                return {
                    "success": result.exit_code == 0,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout[:8000] if result.stdout else "",
                    "stderr": result.stderr[:2000] if result.stderr else "",
                }

            # === Web & research ===
            elif tool_name == "web_search":
                query = tool_args.get("query", "")
                deep = tool_args.get("deep", False)
                result = await self.models.web_search(query, reasoning=deep)
                return {
                    "content": result.get("content", "")[:8000],
                    "thinking": result.get("thinking", "")[:2000] if deep else None,
                }

            # === Git operations ===
            elif tool_name == "commit":
                message = tool_args.get("message", "Automated commit")
                # Sanitize commit message to prevent injection
                safe_message = shlex.quote(message)
                await self.sandboxes.execute(sandbox.id, "git add -A")
                result = await self.sandboxes.execute(sandbox.id, f"git commit -m {safe_message}")

                if result.exit_code == 0:
                    sha_result = await self.sandboxes.execute(sandbox.id, "git rev-parse HEAD")
                    sha = sha_result.stdout.strip()[:8]
                    return {"success": True, "commit_sha": sha, "message": f"Committed: {sha}"}
                else:
                    return {"success": False, "error": result.stderr or "Commit failed"}

            elif tool_name == "create_branch":
                branch_name = tool_args.get("branch_name", "")
                # Validate branch name (alphanumeric, dash, underscore, slash only)
                if not re.match(r'^[\w\-/]+$', branch_name):
                    return {"success": False, "error": "Invalid branch name - use only alphanumeric, dash, underscore, slash"}
                result = await self.sandboxes.execute(sandbox.id, f"git checkout -b {shlex.quote(branch_name)}")
                return {
                    "success": result.exit_code == 0,
                    "message": f"Created branch {branch_name}" if result.exit_code == 0 else result.stderr,
                }

            elif tool_name == "create_pr":
                title = tool_args.get("title", "")
                body = tool_args.get("body", "")
                base = tool_args.get("base", "main")

                # Validate base branch name
                if not re.match(r'^[\w\-/]+$', base):
                    return {"success": False, "error": "Invalid base branch name"}

                # Push current branch first
                branch_result = await self.sandboxes.execute(sandbox.id, "git rev-parse --abbrev-ref HEAD")
                current_branch = branch_result.stdout.strip()

                # Validate current branch
                if not re.match(r'^[\w\-/]+$', current_branch):
                    return {"success": False, "error": "Invalid current branch name"}

                push_result = await self.sandboxes.execute(
                    sandbox.id, f"git push -u origin {shlex.quote(current_branch)}", timeout=60
                )

                if push_result.exit_code != 0:
                    return {"success": False, "error": f"Push failed: {push_result.stderr}"}

                # Create PR using gh cli with sanitized inputs
                safe_title = shlex.quote(title)
                safe_body = shlex.quote(body)
                safe_base = shlex.quote(base)
                pr_cmd = f"gh pr create --title {safe_title} --body {safe_body} --base {safe_base}"
                pr_result = await self.sandboxes.execute(sandbox.id, pr_cmd, timeout=30)

                if pr_result.exit_code == 0:
                    return {"success": True, "message": "PR created", "output": pr_result.stdout}
                else:
                    return {"success": False, "error": pr_result.stderr or "PR creation failed"}

            # === User interaction ===
            elif tool_name == "ask_user":
                question = tool_args.get("question", "")
                # TODO: Integrate with Discord for real user input
                return {"response": f"[User input requested: {question}]"}

            # === Completion ===
            elif tool_name == "done":
                return {"status": "completed"}

            else:
                return {"error": f"Unknown tool: {tool_name}"}

        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {tool_args.get('file_path', 'unknown')}"}
        except Exception as e:
            logger.exception(f"Tool {tool_name} failed")
            return {"error": str(e)}


# Global instance
autonomous_agent = AutonomousAgent()
