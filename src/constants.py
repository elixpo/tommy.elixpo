"""Constants and configuration values for Polly Helper Bot."""

import os
import re

# API Configuration
API_TIMEOUT = 60  # Keep generous for large repos
POLLINATIONS_API_BASE = "https://gen.pollinations.ai"

# Session Configuration
SESSION_TIMEOUT = 300  # 5 minutes

# Message Limits
MAX_MESSAGE_LENGTH = 2000
MAX_TITLE_LENGTH = 80
MAX_ERROR_LENGTH = 200

# Default Values
DEFAULT_REPO = "pollinations/pollinations"

# Load repo info for AI context
_repo_info_path = os.path.join(os.path.dirname(__file__), "data", "repo_info.txt")
try:
    with open(_repo_info_path, "r", encoding="utf-8") as f:
        REPO_INFO = f.read()
except FileNotFoundError:
    REPO_INFO = "Pollinations.AI - AI media generation platform with image and text generation APIs."

# =============================================================================
# BRIDGE SYSTEM PROMPT - Handles ALL intents (search, lookup, report, etc.)
# =============================================================================

BRIDGE_SYSTEM_PROMPT = """You are Polly, a Discord-to-GitHub Issues bridge bot for Pollinations.AI. You help users search, explore, and create GitHub issues through natural conversation.

## Context about Pollinations.AI:
{repo_info}

## Your Capabilities:
1. **Search issues** - Find issues by keywords, labels, state
2. **Lookup issue** - Get details of a specific issue by number
3. **My issues** - Find issues reported by a Discord user
4. **Report issue** - Create new GitHub issues through conversation
5. **Add to issue** - Comment on existing issues

## Response Format:
ALWAYS respond with a JSON object. Choose the appropriate action:

### To search for issues:
{{
  "action": "search_issues",
  "keywords": "search terms extracted from user query",
  "state": "open" | "closed" | "all",
  "discord_only": false
}}
- state: Default "open". Use "closed" or "all" if user asks about resolved/fixed/closed issues or wants history
- discord_only: true if user specifically asks about Discord-reported issues

### To find a user's issues (Discord):
{{
  "action": "my_issues",
  "discord_username": "username from conversation",
  "state": "open" | "closed" | "all"
}}

### To lookup a specific issue:
{{
  "action": "get_issue",
  "issue_number": 42,
  "include_comments": false
}}
- include_comments: true if user wants to see discussion/comments

### To ask follow-up questions (for issue creation):
{{
  "action": "ask",
  "message": "Your question"
}}

### To create a new issue:
{{
  "action": "create_issue",
  "title": "Clear issue title (max 80 chars)",
  "description": "Full markdown description with all collected details",
  "discord_message": "Created {{link}} for you {{mention}}!"
}}

### To add comment to existing issue:
{{
  "action": "add_to_existing",
  "issue_number": 42,
  "comment": "Formatted comment",
  "discord_message": "Added your info to {{link}} {{mention}}!"
}}

### To cancel:
{{
  "action": "cancel",
  "message": "Friendly goodbye"
}}

### To respond with info (after search/lookup results):
{{
  "action": "respond",
  "message": "Your response summarizing the results"
}}

## Intent Detection Examples:

| User says | Action |
|-----------|--------|
| "find auth bugs" | search_issues (keywords: "auth bugs") |
| "issues about 502 errors" | search_issues (keywords: "502 errors") |
| "my issues" / "what did I report" | my_issues |
| "issues by @someone" | my_issues (extract username) |
| "what's #42" / "issue 42" | get_issue |
| "is #42 fixed?" | get_issue (check state in response) |
| "closed issues about API" | search_issues (state: "closed") |
| "did we fix the login bug" | search_issues (state: "all", keywords: "login bug") |
| "the API is broken" | ask (start issue creation flow) |
| "I want to report a bug" | ask (start issue creation flow) |
| "add this to #42" | add_to_existing |

## Smart State Detection:
- Default to "open" for searches
- Use "closed" when: "fixed", "resolved", "closed", past tense questions
- Use "all" when: "history", "ever", "all time", comparing past/present

## Guidelines:
- Be fast - extract intent and parameters in ONE response
- Be friendly and concise
- For searches: extract good keywords, remove filler words
- For issue creation: ask 1-2 questions at a time, be patient with beginners
- Works in ANY language - respond in user's language, but GitHub content in English
- NEVER make up issue numbers or data

## IMPORTANT - Conversation Context:
You may receive previous messages from the thread. Use this context to understand:
- What the user already asked about
- What issues they were looking at
- Ongoing issue creation conversations

Return ONLY valid JSON, no other text."""

# Format with repo info
BRIDGE_SYSTEM_PROMPT = BRIDGE_SYSTEM_PROMPT.format(repo_info=REPO_INFO)

# =============================================================================
# TOOL DEFINITIONS FOR GEMINI FUNCTION CALLING
# =============================================================================

GITHUB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "github_issue",
            "description": """ALL-IN-ONE issue tool. Use for ANY issue operation:

READ actions:
- action="get" → Get issue details (use issue_number, include_comments)
- action="search" → Search issues (use keywords, state, labels)
- action="search_user" → Find user's issues (use discord_username, state)
- action="find_similar" → Find duplicates (use keywords, limit)
- action="list_labels" → List available labels
- action="list_milestones" → List milestones

WRITE actions:
- action="create" → Create issue (use title, description)
- action="comment" → Add comment (use issue_number, comment)
- action="edit_comment" → Edit bot's own comment (use comment_id, body)
- action="delete_comment" → Delete bot's own comment (use comment_id)

ADMIN actions (require admin role):
- action="close" → Close issue (use issue_number, reason, comment)
- action="reopen" → Reopen issue (use issue_number, comment)
- action="edit" → Edit issue (use issue_number, title, body)
- action="label" → Add labels (use issue_number, labels)
- action="unlabel" → Remove labels (use issue_number, labels)
- action="assign" → Assign users (use issue_number, assignees)
- action="unassign" → Unassign users (use issue_number, assignees)
- action="milestone" → Set milestone (use issue_number, milestone)
- action="lock" → Lock/unlock (use issue_number, lock, reason)
- action="link" → Link issues (use issue_number, related_issues, relationship)

SUBSCRIPTION actions:
- action="subscribe" → Subscribe to updates (use issue_number)
- action="unsubscribe" → Unsubscribe (use issue_number)
- action="unsubscribe_all" → Unsubscribe from all
- action="list_subscriptions" → Show subscriptions

SUB-ISSUE actions:
- action="get_sub_issues" → Get sub-issues/children (use issue_number)
- action="get_parent" → Get parent issue (use issue_number)
- action="create_sub_issue" → Create NEW issue as sub-issue (use issue_number=parent, title, description) [admin]
- action="add_sub_issue" → Link existing issue as child (use issue_number=parent, child_issue_number) [admin]
- action="remove_sub_issue" → Unlink child from parent (use issue_number=parent, child_issue_number) [admin]

NOTE: Sub-issues are regular issues! Use any action (comment, close, assign, label, etc.) on them by their issue_number.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The action to perform (get, search, create, close, comment, etc.)"
                    },
                    "issue_number": {
                        "type": "integer",
                        "description": "Issue number (for get, close, comment, edit, label, assign, etc.)"
                    },
                    "keywords": {
                        "type": "string",
                        "description": "Search terms (for search, find_similar)"
                    },
                    "state": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "description": "Filter state (for search actions)"
                    },
                    "title": {
                        "type": "string",
                        "description": "Issue title (for create, edit)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Issue body/description (for create)"
                    },
                    "body": {
                        "type": "string",
                        "description": "New body text (for edit)"
                    },
                    "comment": {
                        "type": "string",
                        "description": "Comment text (for comment, close, reopen)"
                    },
                    "reason": {
                        "type": "string",
                        "enum": ["completed", "not_planned", "duplicate"],
                        "description": "Close reason (for close)"
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Labels (for label, unlabel, search)"
                    },
                    "assignees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "GitHub usernames (for assign, unassign)"
                    },
                    "milestone": {
                        "type": "string",
                        "description": "Milestone name or 'none' (for milestone)"
                    },
                    "lock": {
                        "type": "boolean",
                        "description": "True to lock, false to unlock (for lock)"
                    },
                    "related_issues": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Related issue numbers (for link)"
                    },
                    "relationship": {
                        "type": "string",
                        "enum": ["duplicate", "related", "blocks", "blocked_by", "parent", "child"],
                        "description": "Relationship type (for link)"
                    },
                    "discord_username": {
                        "type": "string",
                        "description": "Discord username (for search_user)"
                    },
                    "include_comments": {
                        "type": "boolean",
                        "description": "Include comments (for get)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (for search, find_similar)"
                    },
                    "child_issue_number": {
                        "type": "integer",
                        "description": "Child/sub-issue number (for add_sub_issue, remove_sub_issue)"
                    },
                    "comment_id": {
                        "type": "integer",
                        "description": "Comment ID (for edit_comment, delete_comment) - get from issue comments"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_project",
            "description": """ALL-IN-ONE GitHub Projects V2 tool. Use for ANY project operation:

READ actions:
- action="list" → List ALL projects in organization (no project_number needed!)
- action="view" → View project board (use project_number)
- action="list_items" → List items in project (use project_number, status)
- action="get_item" → Get item details (use project_number, issue_number)

WRITE actions (admin only):
- action="add" → Add issue to project (use project_number, issue_number)
- action="remove" → Remove from project (use project_number, issue_number)
- action="set_status" → Update status/column (use project_number, issue_number, status)
- action="set_field" → Set custom field (use project_number, issue_number, field_name, field_value)

Examples:
- "Show all projects" → action="list"
- "What's in project 20?" → action="view", project_number=20
- "Add #123 to project 20" → action="add", project_number=20, issue_number=123
- "Move #123 to In Progress" → action="set_status", project_number=20, issue_number=123, status="In Progress" """,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The action: list, view, list_items, get_item, add, remove, set_status, set_field"
                    },
                    "project_number": {
                        "type": "integer",
                        "description": "Project number from URL (e.g., 20 from projects/20). NOT required for action='list'"
                    },
                    "issue_number": {
                        "type": "integer",
                        "description": "Issue number to add/update"
                    },
                    "status": {
                        "type": "string",
                        "description": "Status/column name (e.g., 'Todo', 'In Progress', 'Done')"
                    },
                    "field_name": {
                        "type": "string",
                        "description": "Custom field name (for set_field)"
                    },
                    "field_value": {
                        "type": "string",
                        "description": "Field value to set (for set_field)"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_overview",
            "description": """FAST: Get complete repo context in ONE call. Returns issues, labels, milestones, and projects together.

Use this FIRST when you need multiple pieces of info:
- "Show me the repo" → Get overview
- "What issues are there?" → Get overview (faster than searching)
- "What labels/milestones exist?" → Get overview

Returns: issue counts, recent issues, all labels (with counts), milestones, and top projects.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "issues_limit": {
                        "type": "integer",
                        "description": "Number of recent issues to include (default 10, max 50)"
                    },
                    "include_projects": {
                        "type": "boolean",
                        "description": "Include projects list (default true)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_custom",
            "description": """FLEXIBLE: Fetch raw GitHub data for YOUR analysis. Use for anything not covered by other tools!

Examples:
- "find spam issues" → request: "all open issues with full body text", include_body=true
- "stale issues?" → request: "issues with no activity in 30+ days"
- "who's most active?" → request: "recent commits and PRs"
- "PR stats" → request: "pull request counts"
- "repo health" → request: "repository statistics"

The tool fetches raw data. YOU analyze it and answer the user's question!""",
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": "What data you need in plain English"
                    },
                    "include_body": {
                        "type": "boolean",
                        "description": "Include full body text? (for spam detection, etc.)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max items (default 50, max 100)"
                    }
                },
                "required": ["request"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_pr",
            "description": """ALL-IN-ONE Pull Request tool. Use for ANY PR operation:

READ actions:
- action="get" → Get full PR details (use pr_number)
- action="list" → List PRs (use state: open/closed/merged, limit, base)
- action="get_files" → Get files changed (use pr_number)
- action="get_diff" → Get unified diff (use pr_number)
- action="get_checks" → Get CI/workflow status (use pr_number)
- action="get_commits" → Get all commits in PR (use pr_number)
- action="get_threads" → Get review threads (use pr_number)
- action="get_review_comments" → Get inline review comments (use pr_number)
- action="get_file_at_ref" → Get FULL file content at a branch/commit (use file_path, ref)
  - Use this to see actual file contents, not just diffs!
  - file_path: e.g., ".github/workflows/ci.yml"
  - ref: branch name (e.g., "feat-branch") or commit SHA

WRITE actions (admin only):
- action="request_review" → Request reviewers (use pr_number, reviewers, team_reviewers)
- action="remove_reviewer" → Remove requested reviewers (use pr_number, reviewers, team_reviewers)
- action="approve" → Approve PR (use pr_number, optional body)
- action="request_changes" → Request changes (use pr_number, body required)
- action="merge" → Merge PR (use pr_number, merge_method: merge/squash/rebase)
- action="update" → Update PR title/body (use pr_number, title, body)
- action="close" → Close PR (use pr_number)
- action="reopen" → Reopen PR (use pr_number)
- action="create" → Create new PR (use title, head, base, body, draft)
- action="convert_to_draft" → Convert to draft (use pr_number)
- action="ready_for_review" → Mark ready (use pr_number)
- action="update_branch" → Update with base branch (use pr_number)
- action="comment" → Add comment (use pr_number, comment)
- action="inline_comment" → Comment on specific line (use pr_number, path, line, comment, side)
- action="suggest" → Add code suggestion (use pr_number, path, line, suggestion, comment)
- action="resolve_thread" → Resolve review thread (use thread_id)
- action="unresolve_thread" → Unresolve review thread (use thread_id)
- action="enable_auto_merge" → Enable auto-merge (use pr_number, merge_method)
- action="disable_auto_merge" → Disable auto-merge (use pr_number)

AI REVIEW:
- action="review" → AI code review (use pr_number, post_review_to_github: true/false)
  - post_review_to_github=false → Returns review in Discord (default)
  - post_review_to_github=true → Posts review as GitHub comment

Examples:
- "Show open PRs" → action="list", state="open"
- "What's in PR #123?" → action="get", pr_number=123
- "Request review from alice" → action="request_review", pr_number=123, reviewers=["alice"]
- "Merge #123" → action="merge", pr_number=123
- "Review PR #123" → action="review", pr_number=123
- "Comment on line 42 of main.py" → action="inline_comment", pr_number=123, path="main.py", line=42, comment="Fix this"
- "Suggest fix" → action="suggest", pr_number=123, path="main.py", line=42, suggestion="fixed_code()"
- "Enable auto-merge" → action="enable_auto_merge", pr_number=123, merge_method="squash"
- "Show workflow file in PR branch" → action="get_file_at_ref", file_path=".github/workflows/ci.yml", ref="feat-branch" """,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "The action: get, list, get_files, get_diff, get_checks, get_commits, get_threads, get_review_comments, get_file_at_ref, request_review, remove_reviewer, approve, request_changes, merge, update, close, reopen, create, convert_to_draft, ready_for_review, update_branch, comment, inline_comment, suggest, resolve_thread, unresolve_thread, enable_auto_merge, disable_auto_merge, review"
                    },
                    "pr_number": {
                        "type": "integer",
                        "description": "PR number (for most actions)"
                    },
                    "state": {
                        "type": "string",
                        "enum": ["open", "closed", "merged", "all"],
                        "description": "Filter state (for list)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (for list, default 10)"
                    },
                    "base": {
                        "type": "string",
                        "description": "Base branch filter (for list, create)"
                    },
                    "title": {
                        "type": "string",
                        "description": "PR title (for create, update)"
                    },
                    "body": {
                        "type": "string",
                        "description": "PR body or review comment (for create, update, approve, request_changes)"
                    },
                    "head": {
                        "type": "string",
                        "description": "Head branch name (for create)"
                    },
                    "draft": {
                        "type": "boolean",
                        "description": "Create as draft (for create)"
                    },
                    "reviewers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "GitHub usernames (for request_review, remove_reviewer)"
                    },
                    "team_reviewers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Team slugs (for request_review, remove_reviewer)"
                    },
                    "merge_method": {
                        "type": "string",
                        "enum": ["merge", "squash", "rebase"],
                        "description": "Merge method (for merge, enable_auto_merge)"
                    },
                    "commit_title": {
                        "type": "string",
                        "description": "Custom merge commit title (for merge)"
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Custom merge commit message (for merge)"
                    },
                    "comment": {
                        "type": "string",
                        "description": "Comment text (for comment, inline_comment, suggest)"
                    },
                    "post_review_to_github": {
                        "type": "boolean",
                        "description": "Post AI review as GitHub comment? (for review action, default false)"
                    },
                    "path": {
                        "type": "string",
                        "description": "File path for inline comment/suggestion (e.g., 'src/main.py')"
                    },
                    "line": {
                        "type": "integer",
                        "description": "Line number for inline comment/suggestion"
                    },
                    "side": {
                        "type": "string",
                        "enum": ["LEFT", "RIGHT"],
                        "description": "Diff side: LEFT=deletions, RIGHT=additions (default RIGHT)"
                    },
                    "suggestion": {
                        "type": "string",
                        "description": "Suggested code replacement (for suggest action)"
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Review thread ID (for resolve_thread, unresolve_thread)"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "File path for get_file_at_ref (e.g., '.github/workflows/ci.yml', 'src/main.py')"
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git ref (branch name, tag, or commit SHA) for get_file_at_ref"
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "github_code",
            "description": """CODE AGENT - Flexible git operations and autonomous coding.

FULL TASK WORKFLOW:
- action="task" → Full autonomous task (understand → plan → code → test → fix → commit)
- action="task" + test_only=true → Code and test only, NO commit/PR (user decides after seeing results)
- action="plan" → Just create a plan without executing
- action="status" → Check status of running task
- action="destroy_sandbox" → Destroy a sandbox (needs: sandbox_id) - only sandbox creator can confirm

SANDBOX OPERATIONS (use sandbox_id from previous task):
- action="run_in_sandbox" → Run command in existing sandbox (needs: sandbox_id, command)
- action="read_sandbox_file" → Read file from sandbox (needs: sandbox_id, file_path)
- action="write_sandbox_file" → Write file to sandbox (needs: sandbox_id, file_path, file_content)
- action="destroy_sandbox" → Destroy sandbox (needs: sandbox_id) - only creator can confirm

FLEXIBLE GIT OPERATIONS (admin only):
- action="list_branches" → List all branches in repo
- action="create_branch" → Create new branch (needs: new_branch)
- action="delete_branch" → Delete a branch (needs: new_branch) - NOT main/master!
- action="read_file" → Read file from repo (needs: file_path)
- action="list_files" → List files in repo (optional: pattern glob)
- action="edit_file" → Edit repo file via API (needs: file_path, file_content, optional: old_content)
- action="commit" → Commit changes (needs: commit_message)
- action="push" → Push commits to remote
- action="open_pr" → Create PR (needs: pr_title, optional: pr_body, base_branch)

EXAMPLES:
- "Create a branch for the fix" → action="create_branch", new_branch="fix/auth-bug"
- "Edit README.md" → action="edit_file", file_path="README.md", file_content="new content"
- "Replace old code with new" → action="edit_file", file_path="x.py", old_content="old", file_content="new"
- "Commit the changes" → action="commit", commit_message="Fix auth bug"
- "Push to remote" → action="push"
- "Open PR" → action="open_pr", pr_title="Fix auth", base_branch="main"
- "Full task with PR" → action="task", task="Fix auth bug", create_pr=true

SANDBOX WORKFLOW (Interactive):
The "task" action creates an isolated sandbox that PERSISTS for follow-up actions.
1. User asks for code task → AI clarifies scope first
2. AI runs task (sandbox: code → test → results) with sandbox_id returned
3. AI reports results and asks: "Want to run more tests? Create PR? Or destroy sandbox?"
4. User can reply with follow-up actions:
   - "test 120+021" → AI uses run_in_sandbox to test
   - "create branch and PR" → AI uses create_branch, commit, push, open_pr
   - "destroy sandbox" → AI uses destroy_sandbox
5. Sandbox auto-expires after 1 hour if not destroyed

IMPORTANT - ALWAYS ASK FIRST:
Before taking action, clarify with the user:
- What exactly should be done?
- After task completes: ask about next steps (more tests? PR? destroy?)
- Never assume - ask if unclear!

Read-only actions (read_file, list_files, list_branches) are safe.
Write actions need explicit user confirmation before executing.

NOTE: All github_code actions require admin permissions!""",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["task", "plan", "status", "run_in_sandbox", "read_sandbox_file", "write_sandbox_file", "destroy_sandbox", "list_branches", "create_branch", "delete_branch", "read_file", "list_files", "edit_file", "commit", "push", "open_pr"],
                        "description": "Action to perform"
                    },
                    "sandbox_id": {
                        "type": "string",
                        "description": "Sandbox ID for sandbox operations (from previous task result)"
                    },
                    "command": {
                        "type": "string",
                        "description": "Shell command to run in sandbox (for run_in_sandbox)"
                    },
                    "task": {
                        "type": "string",
                        "description": "Task description (for task/plan actions)"
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository in owner/repo format (default: pollinations/pollinations)"
                    },
                    "branch": {
                        "type": "string",
                        "description": "Working branch (default: main)"
                    },
                    "new_branch": {
                        "type": "string",
                        "description": "Branch name for create_branch/delete_branch"
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to file for read_file/edit_file"
                    },
                    "file_content": {
                        "type": "string",
                        "description": "New content for file (edit_file)"
                    },
                    "old_content": {
                        "type": "string",
                        "description": "Old content to find and replace (edit_file search/replace mode)"
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Commit message (for commit action)"
                    },
                    "pr_title": {
                        "type": "string",
                        "description": "PR title (for open_pr)"
                    },
                    "pr_body": {
                        "type": "string",
                        "description": "PR description (for open_pr)"
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "Base branch for PR (default: main)"
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern for list_files (e.g., '*.py')"
                    },
                    "create_pr": {
                        "type": "boolean",
                        "description": "Create PR after task completion"
                    },
                    "test_only": {
                        "type": "boolean",
                        "description": "Stop after testing - don't commit or PR. Use this to test code without making changes to repo. User can then decide to commit/PR separately."
                    },
                    "max_fix_attempts": {
                        "type": "integer",
                        "description": "Max attempts to fix failing tests (default: 5)"
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID for status checks"
                    }
                },
                "required": ["action"]
            }
        }
    }
]

# =============================================================================
# CODE SEARCH TOOL (only available when LOCAL_EMBEDDINGS_ENABLED=true)
# =============================================================================

CODE_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "code_search",
        "description": """Semantic code search across the Pollinations repository.

Use this to find code by meaning, not just keywords. Great for:
- "How does image generation work?" → finds image gen code
- "Where is rate limiting implemented?" → finds rate limit logic
- "Authentication/login code" → finds auth-related code
- "Error handling for API requests" → finds error handling

Returns relevant code snippets with file paths and line numbers.
This is READ-ONLY - anyone can use it to understand the codebase.""",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query describing what code you're looking for"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5, max: 10)"
                }
            },
            "required": ["query"]
        }
    }
}

# =============================================================================
# WEB SEARCH TOOL - Uses Perplexity models via Pollinations API
# =============================================================================

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": """Real-time web search using Perplexity AI models.

Use this when you need current/real-time information that's not in your training data:
- "What's the latest news about X?"
- "Current price of Bitcoin"
- "Recent updates to React 19"
- "Who won yesterday's game?"
- Technical documentation lookups
- API references and changelogs

Models:
- mode="fast" (default) → Quick factual lookups, simple questions
- mode="reasoning" → Complex research, analysis, multi-step questions

Returns comprehensive answers with source citations.""",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query - be specific for better results"
                },
                "mode": {
                    "type": "string",
                    "enum": ["fast", "reasoning"],
                    "description": "fast=quick lookups (default), reasoning=complex analysis"
                }
            },
            "required": ["query"]
        }
    }
}


def get_tools_with_embeddings(base_tools: list, embeddings_enabled: bool) -> list:
    """Get tool list with optional features."""
    tools = base_tools.copy()

    # Always include web_search
    tools.append(WEB_SEARCH_TOOL)

    # Conditionally include code_search if embeddings enabled
    if embeddings_enabled:
        tools.append(CODE_SEARCH_TOOL)

    return tools


# NOTE: Admin action checks are now handled in bot.py with tool-specific sets:
# - ISSUE_ADMIN_ACTIONS: close, reopen, edit, label, unlabel, assign, unassign, milestone, lock, link, create_sub_issue, add_sub_issue, remove_sub_issue
# - PR_ADMIN_ACTIONS: request_review, remove_reviewer, approve, request_changes, merge, update, create, convert_to_draft, ready_for_review, update_branch, inline_comment, suggest, resolve_thread, unresolve_thread, enable_auto_merge, disable_auto_merge, close, reopen
# - PROJECT_ADMIN_ACTIONS: add, remove, set_status, set_field
# - github_code: ALL actions require admin

# Risky actions that require user confirmation even for admins
# These are destructive or hard-to-reverse operations
RISKY_ACTIONS = {
    # github_issue
    "close": "close this issue",
    "lock": "lock this issue",
    "edit": "edit this issue's title/body",
    # github_pr
    "merge": "merge this PR",
    "close": "close this PR",
    "request_changes": "request changes on this PR",
    # github_code
    "delete_branch": "delete this branch",
    # github_project
    "remove": "remove this item from project",
}

# =============================================================================
# SMART TOOL FILTERING - Match user intent to relevant tools
# =============================================================================

# Keywords that indicate which tool(s) to use
# Compiled regex patterns for fast matching
# Note: Use word boundaries but allow plurals with optional 's'
TOOL_KEYWORDS = {
    "github_issue": re.compile(
        r'\b(issues?|bugs?|reports?|#\d+|problems?|errors?|feature requests?|enhancements?|'
        r'subscrib\w*|labels?|assign\w*|close[ds]?|reopen\w*|milestones?|'
        r'sub.?issues?|child|parent|my issues|duplicates?|similar|ticket)\b',
        re.IGNORECASE
    ),
    "github_pr": re.compile(
        r'\b(prs?|pull\s*requests?|merge[ds]?|review\w*|approv\w*|diffs?|'
        r'checks?|ci|workflow|drafts?|auto.?merge)\b',
        re.IGNORECASE
    ),
    "github_project": re.compile(
        r'\b(projects?\s*(board)?|boards?|kanban|sprint|columns?|todo|in\s*progress|done|backlog)\b',
        re.IGNORECASE
    ),
    "github_code": re.compile(
        r'\b(implement\w*|refactor\w*|coding\s*agent|autonomous|'
        r'fix\s*(issue|bug|this|it|the)|make\s*(branch|changes)|'
        r'edit\s*(the\s*)?(code|file|readme)|update\s*(the\s*)?(code|file)|'
        r'create\s*(a\s*)?branch|new\s*branch|delete\s*branch|'
        r'commit|push\s*(to\s*)?|read\s*file|list\s*files|write\s*code)\b',
        re.IGNORECASE
    ),
    "github_custom": re.compile(
        r'\b(stats?|statistics?|activit\w*|stale|spam|health|contributors?|history)\b',
        re.IGNORECASE
    ),
    "github_overview": re.compile(
        r'\b(overview|summary|show\s*(me\s*)?(the\s*)?repo|what.*(issues|labels|milestones).*exist|'
        r'whats?\s*(in\s*)?the\s*repo|repo\s*(status|info))\b',
        re.IGNORECASE
    ),
    # NOTE: web_search and code_search are NOT filtered by keywords
    # AI decides when to use them based on context - they're always available
}

def filter_tools_by_intent(user_message: str, all_tools: list[dict]) -> list[dict]:
    """
    Filter tools based on user intent keywords.
    Fast regex matching - no API calls.

    Args:
        user_message: The user's message
        all_tools: Full list of tool definitions

    Returns:
        Filtered list of relevant tools, or all tools if no match
    """
    matched_tools = set()
    message_lower = user_message.lower()

    # Check each tool's keywords against the message
    for tool_name, pattern in TOOL_KEYWORDS.items():
        if pattern.search(message_lower):
            matched_tools.add(tool_name)

    # If no matches, return all tools (safe fallback)
    if not matched_tools:
        return all_tools

    # Always include github_issue if user mentions a number like #123
    if re.search(r'#\d+', user_message):
        matched_tools.add("github_issue")
        # Could be PR too - add if not already filtering for something specific
        if len(matched_tools) == 1:
            matched_tools.add("github_pr")

    # Always include these tools - AI decides when to use them
    AI_CONTROLLED_TOOLS = {"web_search", "code_search"}

    # Filter tools list
    filtered = [
        tool for tool in all_tools
        if tool.get("function", {}).get("name") in matched_tools
        or tool.get("function", {}).get("name") in AI_CONTROLLED_TOOLS
    ]

    # Return filtered if we got matches, otherwise all (safety)
    return filtered if filtered else all_tools

# =============================================================================
# TOOL-BASED SYSTEM PROMPT - AI has FULL AUTONOMY
# =============================================================================

TOOL_SYSTEM_PROMPT = """You are Polly, a GitHub assistant for Pollinations.AI. SPEED IS EVERYTHING.

## Current Time: {current_utc}

## Project Context:
{repo_info}

## Where You Operate:
- **Discord**: Users @mention you in channels/threads
- **GitHub**: Users @mention you in issues, PRs, comments, and reviews
- You have FULL access to issues, PRs, projects, code, and repository operations
- This is bidirectional - you respond wherever you're mentioned!

## Tools:
- `github_overview` - FAST: Get issues + labels + milestones + projects in ONE call (use first!)
- `github_issue` - All issue ops (get, search, create, comment, close, label, assign, sub-issues, etc.)
- `github_pr` - All PR ops (get, list, review, approve, merge, inline comments, suggestions, etc.)
- `github_project` - Projects V2 (list, view, add, set_status, set_field)
- `github_code` - Code agent for autonomous coding tasks (create branches, edit files, open PRs)
- `github_custom` - Flexible data fetching for analysis
- `web_search` - Real-time web search (news, docs, current info). Use mode="fast" for quick lookups, mode="reasoning" for complex questions
- `code_search` - Semantic code search (if enabled) - find code by meaning

Admin actions (close, edit, label, assign, merge, approve, request_review, code edits, etc.) require admin role.

## SPEED - BATCH ALL TOOL CALLS:
CRITICAL: Make ALL needed tool calls in ONE response. NEVER make one call, wait, then make another.
- Need to get issue AND search? → Call BOTH tools in same response
- Need issues + labels + project info? → Call ALL 3 in one go
- User reports problem? → Call `find_similar` immediately in FIRST response
- Multiple lookups? → Call them ALL together, NOT one at a time

BAD (slow - 3 round trips):
1. Call get_issue → wait → 2. Call list_labels → wait → 3. Call search

GOOD (fast - 1 round trip):
1. Call get_issue + list_labels + search all at once → respond with everything

## Be Proactive - USE TOOLS FIRST, ASK LATER:
CRITICAL: You have tools to get ANY info you need. NEVER ask the user for info you can fetch yourself!

- User mentions issue/PR number? → Call `github_issue` or `github_pr` to GET it, don't ask for details
- User mentions repo? → You can access ANY public repo, not just pollinations/pollinations
- Need context about a problem? → Call `github_issue` with action="search" or use `github_custom`
- User mentions problem? → Immediately call `find_similar` to check for duplicates
- Need file contents? → Use `github_pr` action="get_file" or `github_code` to read it
- Vague question? → Search/fetch first, THEN ask for specifics only if tools don't help
- User's issue matches existing one? → Add their info as a comment

BAD: "Can you provide the issue URL?" or "What repo is this in?"
GOOD: Call github_issue/github_pr with what you know, infer repo from context, search if needed

The user mentioned it = you can look it up. Only ask when info truly doesn't exist in GitHub.

## Response Style:
**Discord:** Concise, no fluff, bullet points, match user's energy
**GitHub issues:** Clear title, structured body (Problem → Steps → Expected → Actual)

## Link Formatting (CRITICAL - ALWAYS INCLUDE LINKS):
EVERY reference to a GitHub resource MUST include a clickable link for quick access!
Users need links to verify info, take action, or navigate quickly - never make them search.

**Format:** `[visible text](<url>)` - angle brackets suppress Discord previews

**Always link these:**
- Issues/PRs: `[#123](<https://github.com/owner/repo/issues/123>)` or `[#123](<https://github.com/owner/repo/pull/123>)`
- Users: `[@username](<https://github.com/username>)`
- Files: `[filename.py](<https://github.com/owner/repo/blob/main/path/filename.py>)`
- Commits: `[abc1234](<https://github.com/owner/repo/commit/abc1234>)`
- Branches: `[branch-name](<https://github.com/owner/repo/tree/branch-name>)`
- Projects: `[Project Name](<https://github.com/orgs/owner/projects/1>)`
- Workflows/Actions: `[workflow](<https://github.com/owner/repo/actions/runs/123>)`

**Examples:**
- Single: `Created [#456](<https://...>) - Add dark mode`
- List: `• [#123](<url>) Fix login bug` / `• [#124](<url>) Add tests`
- PR by user: `[#789](<url>) by [@alice](<https://github.com/alice>)`

**Bad (never do this):**
- `#123` without link - user can't click it
- `https://github.com/...` bare URL - creates embed spam
- `[#123](url)` without `<>` - creates embed spam

## Multilingual:
Offer to chat in user's language, but GitHub content MUST be in English.

## Hard Rules:
1. NEVER make up issue numbers or fake data
2. NEVER create duplicates without checking first
3. NEVER use @mentions in GitHub (use backticks: `username`)
4. NEVER retry failed tools blindly - ask user for clarification
5. On errors, tell user clearly and offer alternatives
6. Discord usernames ≠ GitHub usernames. If you need to assign/mention someone on GitHub, ask for their GitHub username first - don't assume it matches their Discord name
7. If the user's request is unclear or you're unsure what they want, ASK FOLLOW-UP QUESTIONS instead of guessing or calling tools repeatedly
8. NEVER return empty responses - always say something helpful, even if just asking for clarification
9. For CODE ACTIONS (github_code): ALWAYS ask before write operations! Clarify: read-only vs changes? branch? PR? commit? NEVER assume - the user must explicitly confirm what actions to take

## RISKY ACTIONS - ADMIN ONLY:
These actions require admin privileges: merge, close, delete_branch, lock, request_changes, edit issue/PR, remove from project, assign, add/remove labels, set milestone.

**Non-admins:** Do NOT ask for confirmation on admin actions - they can't do them anyway.

**Admins:** Ask for confirmation before destructive operations like merge, close, delete, lock, or bulk edits. Wait for "yes"/"confirm" before executing.

## CONFIRMATION MUST COME FROM ORIGINAL REQUESTER (admins only):
When you ask an admin for confirmation:
1. REMEMBER who made the original request
2. Only accept confirmation from that same user
3. If a different user tries to confirm, tell them only the original requester can approve
This prevents unauthorized users from approving destructive operations they didn't initiate."""

def get_tool_system_prompt() -> str:
    """Get the tool system prompt with current UTC time."""
    from datetime import datetime, timezone
    current_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return TOOL_SYSTEM_PROMPT.format(repo_info=REPO_INFO, current_utc=current_utc)


# Keep static version for backwards compatibility (without dynamic time)
TOOL_SYSTEM_PROMPT_STATIC = TOOL_SYSTEM_PROMPT.format(repo_info=REPO_INFO, current_utc="[dynamic]")

# =============================================================================
# LEGACY - Keep for backwards compatibility during transition
# =============================================================================

CONVERSATION_SYSTEM_PROMPT = BRIDGE_SYSTEM_PROMPT
