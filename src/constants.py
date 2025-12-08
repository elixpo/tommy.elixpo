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
            "description": """Issue operations.

Actions:
- get: Get issue (issue_number, include_comments)
- search: General issue search with filters (keywords, state, labels)
- search_user: User's issues by discord username (discord_username, state)
- find_similar: Find potential DUPLICATES before creating new issue (keywords, limit)
- list_labels / list_milestones: List available
- create: New issue (title, description)
- comment: Add comment WITHOUT closing (issue_number, comment) - don't use if closing!
- edit_comment / delete_comment: Modify bot's comments (comment_id)
- close: Close WITH comment in ONE call (issue_number, reason, comment) - includes the comment! [admin]
- reopen: Reopen issue (issue_number, comment) [admin]
- edit: Edit title/body (issue_number, title, body) [admin]
- label/unlabel: Manage labels (issue_number, labels) [admin]
- assign/unassign: Manage assignees (issue_number, assignees) [admin]
- milestone: Set milestone (issue_number, milestone) [admin]
- lock: Lock/unlock (issue_number, lock, reason) [admin]
- link: Link issues (issue_number, related_issues, relationship) [admin]
- subscribe/unsubscribe/unsubscribe_all/list_subscriptions: Notifications
- get_sub_issues/get_parent: Sub-issue hierarchy
- create_sub_issue/add_sub_issue/remove_sub_issue: Manage sub-issues [admin]""",
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
            "description": """GitHub Projects V2 operations.

Actions:
- list: List all org projects
- view: View project board (project_number)
- list_items: List items (project_number, status)
- get_item: Get item details (project_number, issue_number)
- add: Add issue to project (project_number, issue_number) [admin]
- remove: Remove from project (project_number, issue_number) [admin]
- set_status: Update column (project_number, issue_number, status) [admin]
- set_field: Set custom field (project_number, issue_number, field_name, field_value) [admin]""",
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
            "description": """Get repo summary in ONE call: issue counts, recent issues, labels, milestones, projects. Use first for context.""",
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
            "description": """Fetch raw GitHub data for custom analysis.
Use for: commit history, contributor stats, activity metrics, stale issue detection, spam detection.
NOT for: creating/editing issues (use github_issue), PRs (use github_pr), code changes (use polly_agent).""",
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
            "description": """Pull Request operations.

Actions:
- get: Get PR details (pr_number)
- list: List PRs (state, limit, base)
- get_files/get_diff/get_checks/get_commits: PR details (pr_number)
- get_threads/get_review_comments: Review discussions (pr_number)
- get_file_at_ref: Get file content at branch/commit (file_path, ref)
- review: AI code review analyzing bugs/security/perf (pr_number, post_review_to_github=true to post as GitHub comment)
- comment: Add comment (pr_number, comment)
- inline_comment: Comment on line (pr_number, path, line, comment, side) [admin]
- suggest: Code suggestion (pr_number, path, line, suggestion) [admin]
- request_review/remove_reviewer: Manage reviewers (pr_number, reviewers) [admin]
- approve: Approve PR (pr_number, body) [admin]
- request_changes: Request changes (pr_number, body) [admin]
- merge: Merge PR (pr_number, merge_method) [admin, confirm]
- close/reopen: (pr_number) [admin, confirm for close]
- create: New PR (title, head, base, body, draft) [admin]
- update: Edit PR (pr_number, title, body) [admin]
- convert_to_draft/ready_for_review: Draft status (pr_number) [admin]
- update_branch: Sync with base (pr_number) [admin]
- resolve_thread/unresolve_thread: Thread status (thread_id) [admin]
- enable_auto_merge/disable_auto_merge: Auto-merge (pr_number, merge_method) [admin]""",
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
            "name": "polly_agent",
            "description": """Polly's coding agent - ONLY for ACTUAL CODE EDITS!

⚠️ STRICT USAGE:
✅ USE: "implement X", "fix bug", "add feature", "edit code", "modify file", "refactor"
❌ NEVER: "search", "find", "show", "read", "list", "what does X do" → use code_search!

WORKFLOW:
1. code_search FIRST → find relevant files
2. action='task' → EDIT code (auto-commits locally, auto-creates branch)
3. action='push' → Push to GitHub (after edits done)
4. action='open_pr' → Create PR (after push)

Example: polly_agent(action='task', task='Fix model routing bug in server.js - change flux fallback to zimage')""",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["task", "status", "list_tasks", "ask_user", "push", "open_pr"],
                        "description": "Action: task (do coding work), push (push to GitHub), open_pr (create PR), status/list_tasks (check progress), ask_user (get user input)"
                    },
                    "task": {
                        "type": "string",
                        "description": "REQUIRED for action='task'. Describe the CODE EDIT to make - what to fix, implement, or modify. Be specific!"
                    },
                    "question": {
                        "type": "string",
                        "description": "Question for ask_user action"
                    },
                    "pr_title": {
                        "type": "string",
                        "description": "PR title (for open_pr)"
                    },
                    "pr_body": {
                        "type": "string",
                        "description": "PR description (for open_pr)"
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository (default: pollinations/pollinations)"
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
        "description": """Semantic code search - find code by meaning. ALWAYS use this BEFORE polly_agent to find relevant files!

Use for: "where is X?", "find the code that does Y", "show me the file for Z", reading/understanding code.
Returns: Code snippets with file paths. Use these to decide what to edit with polly_agent.""",
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
        "description": """Real-time web search for current info.
mode="fast": Quick factual lookups (faster, less tokens)
mode="reasoning": Multi-step analysis with citations (slower, thorough research)""",
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


# =============================================================================
# ADMIN ACTION FILTERING - Hide admin actions from non-admin users
# =============================================================================

# Admin actions per tool - these lines will be removed from descriptions for non-admins
ADMIN_ACTIONS = {
    "github_issue": {
        "close", "reopen", "edit", "label", "unlabel", "assign", "unassign",
        "milestone", "lock", "link", "create_sub_issue", "add_sub_issue", "remove_sub_issue"
    },
    "github_pr": {
        "request_review", "remove_reviewer", "approve", "request_changes", "merge",
        "update", "create", "convert_to_draft", "ready_for_review", "update_branch",
        "inline_comment", "suggest", "resolve_thread", "unresolve_thread",
        "enable_auto_merge", "disable_auto_merge", "close", "reopen"
    },
    "github_project": {
        "add", "remove", "set_status", "set_field"
    },
    # polly_agent is entirely admin-only, handled separately
}


def filter_admin_actions_from_tools(tools: list, is_admin: bool) -> list:
    """
    Filter admin actions from tool descriptions for non-admin users.

    This prevents the AI from even knowing about admin actions, so:
    1. It won't try to call them
    2. It won't suggest them to users
    3. Users can't jailbreak to access them

    Args:
        tools: List of tool definitions
        is_admin: Whether user is admin

    Returns:
        Tools with admin actions removed from descriptions for non-admins
    """
    if is_admin:
        return tools  # Admins see everything

    import copy
    filtered_tools = []

    for tool in tools:
        tool_name = tool.get("function", {}).get("name", "")

        # Skip entirely admin-only tools
        if tool_name == "polly_agent":
            continue

        # Check if this tool has admin actions to filter
        if tool_name not in ADMIN_ACTIONS:
            filtered_tools.append(tool)
            continue

        # Deep copy to avoid modifying original
        tool_copy = copy.deepcopy(tool)
        description = tool_copy["function"]["description"]

        # Remove lines containing [admin] marker
        lines = description.split("\n")
        filtered_lines = [
            line for line in lines
            if "[admin]" not in line.lower()
        ]
        tool_copy["function"]["description"] = "\n".join(filtered_lines)

        # Also filter the action enum if present
        params = tool_copy["function"].get("parameters", {})
        props = params.get("properties", {})
        action_prop = props.get("action", {})

        if "enum" in action_prop:
            admin_actions = ADMIN_ACTIONS.get(tool_name, set())
            action_prop["enum"] = [
                a for a in action_prop["enum"]
                if a not in admin_actions
            ]

        filtered_tools.append(tool_copy)

    return filtered_tools


# NOTE: Admin action checks handled in bot.py. polly_agent write ops require admin, read ops are public.

# Risky actions - AI uses judgment but these are hints for high-risk ops
# The AI decides contextually what needs confirmation based on impact
RISKY_ACTIONS = {
    "merge": "merge this PR",
    "close": "close this",
    "delete_branch": "delete this branch",
    "lock": "lock this issue",
    # AI can also confirm other high-impact ops like bulk edits, force push, etc.
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
    "polly_agent": re.compile(
        # Only match explicit coding/implementation requests - NOT searches/summaries
        r'\b(implement\w*|refactor\w*|coding\s*agent|'
        r'write\s+(the\s+)?(code|function|class|method)|'
        r'edit\s+(the\s+)?(code|file)|modify\s+(the\s+)?(code|file)|'
        r'create\s+(a\s+)?branch|make\s+(a\s+)?branch|new\s+branch|delete\s+branch|'
        r'commit\s+(the\s+)?changes|push\s+(the\s+)?changes|open\s+(a\s+)?pr|'
        r'code\s+this|build\s+this|develop\s+this|'
        r'fix\s+(the\s+)?(bug|issue|error|problem)|'
        r'change\s+(the\s+)?(code|file)|update\s+(the\s+)?(code|file)|'
        r'add\s+(a\s+)?(feature|function|method)|remove\s+(the\s+)?(code|function))\b',
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

def filter_tools_by_intent(user_message: str, all_tools: list[dict], is_admin: bool = False) -> list[dict]:
    """
    Filter tools based on user intent keywords.
    Fast regex matching - no API calls.

    Args:
        user_message: The user's message
        all_tools: Full list of tool definitions
        is_admin: Whether user is admin (polly_agent only for admins)

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
    # polly_agent ONLY for admins (security: it can modify code)
    AI_CONTROLLED_TOOLS = {"web_search", "code_search"}
    if is_admin:
        AI_CONTROLLED_TOOLS.add("polly_agent")

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

TOOL_SYSTEM_PROMPT = """You are Polly, GitHub assistant for Pollinations.AI. Time: {current_utc}

{repo_info}

## Tools
- `github_overview` - Repo summary (issues, labels, milestones, projects)
- `github_issue` - Issues: get, search, create, comment, close, label, assign
- `github_pr` - PRs: get, list, review, approve, merge, inline comments
- `github_project` - Projects V2: list, view, add items, set status
- `polly_agent` - **Code agent** (implement, edit code, create branches, PRs)
- `github_custom` - Raw data (commits, history, stats)
- `web_search` - Web search (mode="fast"|"reasoning")
- `code_search` - Semantic code search

## Behaviors

**PARALLEL CALLS**: Call independent tools together.
- "compare #100 and #200" → github_issue(get 100) + github_issue(get 200)
- "what's in the repo?" → github_overview (NOT polly_agent)

**PROACTIVE**: Fetch data, don't ask. User mentions #123? GET it.

## polly_agent (CODE EDITS ONLY!)
✅ USE: "implement", "fix bug", "edit code", "add feature", "modify file"
❌ NEVER: "search", "find", "show", "read" → use code_search!

**WORKFLOW:**
1. code_search/github tools FIRST → gather context
2. polly_agent(task="Fix X in file Y - [full details]") → edit code
3. polly_agent(push) → push changes | polly_agent(open_pr) → create PR

**RULES:**
- ALWAYS quote agent_response in your reply
- After edits done: use `push`/`open_pr`, NOT `task` again
- Confirm destructive ops (merge, delete, close)

## Style
- Concise bullet points
- **ALWAYS naturally embed ALL links** in responses using Discord markdown:
  - Format: `[visible text](<url>)` (angle brackets around URL are REQUIRED)
  - Examples:
    - "The [Issue #123](<https://github.com/org/repo/issues/123>) is still open"
    - "I found [PR #456](<https://github.com/org/repo/pull/456>) that fixes this"
    - "Check the [README](<https://github.com/org/repo#readme>) for setup"
  - When mentioning multiple items, embed ALL of them:
    - "Looking at [#12](<url>), [#34](<url>), and [#56](<url>), they all relate to..."
  - NEVER post raw URLs - always embed them with descriptive text
- Never fabricate data
- GitHub mentions: `username` not @"""

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
