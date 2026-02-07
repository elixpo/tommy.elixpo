"""GitHub Pull Request operations using GraphQL API.

Provides comprehensive PR management:
- Read: Get PR details, files, reviews, checks, diff
- Write: Request reviewers, merge, approve, create PR
- Review: AI-powered code review (post to GitHub or return to Discord)

Uses GraphQL for most operations, REST API where necessary.
"""
import logging
import re
import aiohttp
from typing import Optional
from dataclasses import dataclass

from ..config import config
from . import github_auth
from .github_graphql import github_graphql

logger = logging.getLogger(__name__)

SKIP_FILE_PATTERNS = [
    re.compile(r"package-lock\.json$"),
    re.compile(r"yarn\.lock$"),
    re.compile(r"pnpm-lock\.yaml$"),
    re.compile(r"\.min\.js$"),
    re.compile(r"\.min\.css$"),
    re.compile(r"\.map$"),
    re.compile(r"\.(svg|png|jpg|jpeg|gif|ico|woff2?|ttf|eot|pyc)$"),
    re.compile(r"__pycache__|\.egg-info"),
    re.compile(r"node_modules/|vendor/|dist/|build/"),
    re.compile(r"migrations/"),
]

HIGH_PRIORITY_PATTERNS = [
    "auth",
    "login",
    "password",
    "secret",
    "token",
    "api",
    "security",
    "crypto",
    "session",
    "credential",
    "key",
    "private",
]

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".cs",
    ".vue",
    ".svelte",
}

CHARS_PER_TOKEN = 4


@dataclass
class FilePatch:
    filename: str
    patch: str
    additions: int
    deletions: int
    priority: int
    tokens: int


class GitHubPRManager:

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def repo(self) -> str:
        return config.github_repo

    @property
    def owner(self) -> str:
        return self.repo.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.repo.split("/")[1]

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60, connect=10)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_token(self) -> Optional[str]:
        if github_auth.github_app_auth:
            token = await github_auth.github_app_auth.get_token()
            if token:
                return token
        return config.github_token if config.github_token else None

    async def _get_headers(self) -> Optional[dict]:
        token = await self._get_token()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _has_auth(self) -> bool:
        return github_auth.github_app_auth is not None or bool(config.github_token)

    async def get_pr(self, pr_number: int) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
            repository(owner: $owner, name: $repo) {
                pullRequest(number: $number) {
                    id
                    number
                    title
                    body
                    state
                    isDraft
                    mergeable
                    url
                    createdAt
                    updatedAt
                    additions
                    deletions
                    changedFiles
                    headRefName
                    headRefOid
                    baseRefName
                    author { login }
                    labels(first: 20) { nodes { name } }
                    assignees(first: 10) { nodes { login } }
                    reviewRequests(first: 10) { nodes { requestedReviewer { ... on User { login } ... on Team { name } } } }
                    reviews(first: 20) {
                        nodes {
                            id
                            state
                            body
                            author { login }
                            submittedAt
                        }
                    }
                    commits(last: 1) {
                        nodes {
                            commit {
                                oid
                                statusCheckRollup {
                                    state
                                    contexts(first: 30) {
                                        nodes {
                                            ... on CheckRun { name status conclusion }
                                            ... on StatusContext { context state }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """

        try:
            result = await github_graphql._execute(
                query,
                {"owner": self.owner, "repo": self.repo_name, "number": pr_number},
            )

            if result.get("error"):
                return {"error": result["error"]}

            pr = result.get("data", {}).get("repository", {}).get("pullRequest")
            if not pr:
                return {"error": f"PR #{pr_number} not found", "not_found": True}

            commit = pr.get("commits", {}).get("nodes", [{}])[0].get("commit", {})
            rollup = commit.get("statusCheckRollup") or {}

            return {
                "number": pr["number"],
                "title": pr["title"],
                "body": pr.get("body") or "",
                "state": pr["state"].lower(),
                "draft": pr["isDraft"],
                "mergeable": pr["mergeable"],
                "url": pr["url"],
                "head": {"ref": pr["headRefName"], "sha": pr["headRefOid"][:7]},
                "base": {"ref": pr["baseRefName"]},
                "author": pr["author"]["login"] if pr.get("author") else "unknown",
                "created_at": pr["createdAt"][:10],
                "updated_at": pr["updatedAt"][:10],
                "additions": pr["additions"],
                "deletions": pr["deletions"],
                "changed_files": pr["changedFiles"],
                "labels": [l["name"] for l in pr.get("labels", {}).get("nodes", [])],
                "assignees": [
                    a["login"] for a in pr.get("assignees", {}).get("nodes", [])
                ],
                "requested_reviewers": [
                    (
                        r["requestedReviewer"]["login"]
                        if "login" in r.get("requestedReviewer", {})
                        else r["requestedReviewer"].get("name", "unknown")
                    )
                    for r in pr.get("reviewRequests", {}).get("nodes", [])
                    if r.get("requestedReviewer")
                ],
                "reviews": [
                    {
                        "user": r["author"]["login"] if r.get("author") else "unknown",
                        "state": r["state"],
                        "body": r.get("body") or "",
                    }
                    for r in pr.get("reviews", {}).get("nodes", [])
                ],
                "checks_state": rollup.get("state", "UNKNOWN"),
                "node_id": pr["id"],
            }
        except Exception as e:
            logger.error(f"Error getting PR: {e}")
            return {"error": str(e)}

    async def list_prs(
        self, state: str = "open", limit: int = 10, base: Optional[str] = None
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        states_map = {
            "open": "OPEN",
            "closed": "CLOSED",
            "merged": "MERGED",
            "all": None,
        }
        gql_state = states_map.get(state.lower())

        query = """
        query($owner: String!, $repo: String!, $limit: Int!, $states: [PullRequestState!]) {
            repository(owner: $owner, name: $repo) {
                pullRequests(first: $limit, states: $states, orderBy: {field: UPDATED_AT, direction: DESC}) {
                    nodes {
                        number
                        title
                        state
                        isDraft
                        url
                        author { login }
                        headRefName
                        baseRefName
                        createdAt
                        updatedAt
                        labels(first: 5) { nodes { name } }
                    }
                }
            }
        }
        """

        variables = {"owner": self.owner, "repo": self.repo_name, "limit": limit}
        if gql_state:
            variables["states"] = [gql_state]

        try:
            result = await github_graphql._execute(query, variables)

            if result.get("error"):
                return {"error": result["error"]}

            prs_data = (
                result.get("data", {})
                .get("repository", {})
                .get("pullRequests", {})
                .get("nodes", [])
            )

            prs = []
            for pr in prs_data:
                if base and pr["baseRefName"] != base:
                    continue
                prs.append(
                    {
                        "number": pr["number"],
                        "title": pr["title"],
                        "state": pr["state"].lower(),
                        "draft": pr["isDraft"],
                        "url": pr["url"],
                        "author": (
                            pr["author"]["login"] if pr.get("author") else "unknown"
                        ),
                        "head": pr["headRefName"],
                        "base": pr["baseRefName"],
                        "created_at": pr["createdAt"][:10],
                        "updated_at": pr["updatedAt"][:10],
                        "labels": [
                            l["name"] for l in pr.get("labels", {}).get("nodes", [])
                        ],
                    }
                )

            return {"prs": prs, "count": len(prs), "state": state}
        except Exception as e:
            logger.error(f"Error listing PRs: {e}")
            return {"error": str(e)}

    async def get_pr_files(self, pr_number: int) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/files?per_page=100"

        try:
            session = await self.get_session()
            async with session.get(url, headers=await self._get_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    files = [
                        {
                            "filename": f["filename"],
                            "status": f["status"],
                            "additions": f["additions"],
                            "deletions": f["deletions"],
                            "changes": f["changes"],
                            "patch": f.get("patch") or None,
                        }
                        for f in data
                    ]
                    return {
                        "pr_number": pr_number,
                        "files": files,
                        "count": len(files),
                        "total_additions": sum(f["additions"] for f in files),
                        "total_deletions": sum(f["deletions"] for f in files),
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error getting PR files: {e}")
            return {"error": str(e)}

    async def get_pr_diff(self, pr_number: int) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}"
        headers = await self._get_headers()
        headers["Accept"] = "application/vnd.github.v3.diff"

        try:
            session = await self.get_session()
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    diff = await response.text()
                    return {"pr_number": pr_number, "diff": diff}
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error getting PR diff: {e}")
            return {"error": str(e)}

    async def get_pr_checks(self, pr_number: int) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
            repository(owner: $owner, name: $repo) {
                pullRequest(number: $number) {
                    commits(last: 1) {
                        nodes {
                            commit {
                                oid
                                statusCheckRollup {
                                    state
                                    contexts(first: 50) {
                                        nodes {
                                            ... on CheckRun {
                                                __typename
                                                name
                                                status
                                                conclusion
                                                detailsUrl
                                            }
                                            ... on StatusContext {
                                                __typename
                                                context
                                                state
                                                targetUrl
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        """

        try:
            result = await github_graphql._execute(
                query,
                {"owner": self.owner, "repo": self.repo_name, "number": pr_number},
            )

            if result.get("error"):
                return {"error": result["error"]}

            pr_data = result.get("data", {}).get("repository", {}).get("pullRequest")
            if not pr_data:
                return {"error": f"PR #{pr_number} not found", "not_found": True}

            commits = pr_data.get("commits", {}).get("nodes", [])
            if not commits:
                return {
                    "pr_number": pr_number,
                    "checks": [],
                    "overall_state": "UNKNOWN",
                }

            commit = commits[0].get("commit", {})
            rollup = commit.get("statusCheckRollup")

            if not rollup:
                return {
                    "pr_number": pr_number,
                    "sha": commit.get("oid", "")[:7],
                    "checks": [],
                    "overall_state": "NO_CHECKS",
                }

            checks = []
            for ctx in rollup.get("contexts", {}).get("nodes", []):
                if ctx.get("__typename") == "CheckRun":
                    checks.append(
                        {
                            "name": ctx.get("name"),
                            "status": ctx.get("status"),
                            "conclusion": ctx.get("conclusion"),
                            "url": ctx.get("detailsUrl"),
                        }
                    )
                elif ctx.get("__typename") == "StatusContext":
                    checks.append(
                        {
                            "name": ctx.get("context"),
                            "state": ctx.get("state"),
                            "url": ctx.get("targetUrl"),
                        }
                    )

            return {
                "pr_number": pr_number,
                "sha": commit.get("oid", "")[:7],
                "overall_state": rollup.get("state", "UNKNOWN"),
                "checks": checks,
                "count": len(checks),
            }
        except Exception as e:
            logger.error(f"Error getting PR checks: {e}")
            return {"error": str(e)}

    async def request_reviewers(
        self,
        pr_number: int,
        reviewers: Optional[list[str]] = None,
        team_reviewers: Optional[list[str]] = None,
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        if not reviewers and not team_reviewers:
            return {"error": "At least one reviewer or team_reviewer required"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/requested_reviewers"
        payload = {}
        if reviewers:
            payload["reviewers"] = reviewers
        if team_reviewers:
            payload["team_reviewers"] = team_reviewers

        try:
            session = await self.get_session()
            async with session.post(
                url, json=payload, headers=await self._get_headers()
            ) as response:
                if response.status in (200, 201):
                    logger.info(f"Requested reviewers for PR #{pr_number}")
                    return {
                        "success": True,
                        "pr_number": pr_number,
                        "reviewers_requested": reviewers or [],
                        "teams_requested": team_reviewers or [],
                        "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                elif response.status == 422:
                    error_data = await response.json()
                    return {
                        "error": f"Cannot request reviewer: {error_data.get('message', 'validation failed')}"
                    }
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error requesting reviewers: {e}")
            return {"error": str(e)}

    async def create_review(
        self, pr_number: int, event: str, body: Optional[str] = None
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        valid_events = ["APPROVE", "REQUEST_CHANGES", "COMMENT"]
        event = event.upper()
        if event not in valid_events:
            return {"error": f"Invalid event. Must be one of: {valid_events}"}

        if event == "REQUEST_CHANGES" and not body:
            return {"error": "Body is required when requesting changes"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/reviews"
        payload = {"event": event}
        if body:
            payload["body"] = body

        try:
            session = await self.get_session()
            async with session.post(
                url, json=payload, headers=await self._get_headers()
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Created {event} review on PR #{pr_number}")
                    return {
                        "success": True,
                        "pr_number": pr_number,
                        "review_id": data.get("id"),
                        "event": event,
                        "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                elif response.status == 422:
                    error_data = await response.json()
                    return {
                        "error": f"Review failed: {error_data.get('message', 'validation failed')}"
                    }
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error creating review: {e}")
            return {"error": str(e)}

    async def merge_pr(
        self,
        pr_number: int,
        commit_title: Optional[str] = None,
        commit_message: Optional[str] = None,
        merge_method: str = "merge",
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        valid_methods = ["merge", "squash", "rebase"]
        if merge_method not in valid_methods:
            return {"error": f"Invalid merge_method. Must be one of: {valid_methods}"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/merge"
        payload = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message

        try:
            session = await self.get_session()
            async with session.put(
                url, json=payload, headers=await self._get_headers()
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Merged PR #{pr_number}")
                    return {
                        "success": True,
                        "pr_number": pr_number,
                        "merged": True,
                        "sha": data.get("sha"),
                        "message": data.get("message"),
                        "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                elif response.status == 405:
                    return {
                        "error": "PR cannot be merged (not mergeable or already merged)"
                    }
                elif response.status == 409:
                    error_data = await response.json()
                    return {
                        "error": f"Merge conflict: {error_data.get('message', 'conflict')}"
                    }
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error merging PR: {e}")
            return {"error": str(e)}

    async def update_pr(
        self,
        pr_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        base: Optional[str] = None,
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        if not any([title, body, state, base]):
            return {"error": "At least one field required"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}"
        payload = {}
        if title:
            payload["title"] = title
        if body:
            payload["body"] = body
        if state and state in ["open", "closed"]:
            payload["state"] = state
        if base:
            payload["base"] = base

        try:
            session = await self.get_session()
            async with session.patch(
                url, json=payload, headers=await self._get_headers()
            ) as response:
                if response.status == 200:
                    return {
                        "success": True,
                        "pr_number": pr_number,
                        "updated": list(payload.keys()),
                        "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error updating PR: {e}")
            return {"error": str(e)}

    async def create_pr(
        self, title: str, body: str, head: str, base: str = "main", draft: bool = False
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        url = f"https://api.github.com/repos/{self.repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
            "draft": draft,
        }

        try:
            session = await self.get_session()
            async with session.post(
                url, json=payload, headers=await self._get_headers()
            ) as response:
                if response.status == 201:
                    data = await response.json()
                    return {
                        "success": True,
                        "pr_number": data["number"],
                        "pr_url": data["html_url"],
                        "state": "draft" if draft else "open",
                    }
                elif response.status == 422:
                    error_data = await response.json()
                    return {
                        "error": f"Cannot create PR: {error_data.get('message', 'validation failed')}"
                    }
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error creating PR: {e}")
            return {"error": str(e)}

    async def convert_to_draft(self, pr_number: int) -> dict:
        pr = await self.get_pr(pr_number)
        if pr.get("error"):
            return pr

        if pr.get("draft"):
            return {
                "success": True,
                "pr_number": pr_number,
                "message": "PR is already a draft",
            }

        mutation = """
        mutation($pullRequestId: ID!) {
            convertPullRequestToDraft(input: {pullRequestId: $pullRequestId}) {
                pullRequest { number isDraft }
            }
        }
        """

        try:
            result = await github_graphql._execute(
                mutation, {"pullRequestId": pr["node_id"]}
            )
            if result.get("error"):
                return {"error": result["error"]}

            return {
                "success": True,
                "pr_number": pr_number,
                "is_draft": True,
                "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
            }
        except Exception as e:
            logger.error(f"Error converting to draft: {e}")
            return {"error": str(e)}

    async def mark_ready_for_review(self, pr_number: int) -> dict:
        pr = await self.get_pr(pr_number)
        if pr.get("error"):
            return pr

        if not pr.get("draft"):
            return {
                "success": True,
                "pr_number": pr_number,
                "message": "PR is already ready for review",
            }

        mutation = """
        mutation($pullRequestId: ID!) {
            markPullRequestReadyForReview(input: {pullRequestId: $pullRequestId}) {
                pullRequest { number isDraft }
            }
        }
        """

        try:
            result = await github_graphql._execute(
                mutation, {"pullRequestId": pr["node_id"]}
            )
            if result.get("error"):
                return {"error": result["error"]}

            return {
                "success": True,
                "pr_number": pr_number,
                "is_draft": False,
                "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
            }
        except Exception as e:
            logger.error(f"Error marking ready: {e}")
            return {"error": str(e)}

    async def update_branch(self, pr_number: int) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        url = (
            f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/update-branch"
        )

        try:
            session = await self.get_session()
            async with session.put(url, headers=await self._get_headers()) as response:
                if response.status == 202:
                    return {
                        "success": True,
                        "pr_number": pr_number,
                        "message": "Branch updated",
                        "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                elif response.status == 422:
                    return {
                        "error": "Branch cannot be updated (no updates available or conflict)"
                    }
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error updating branch: {e}")
            return {"error": str(e)}

    async def add_comment(
        self, pr_number: int, body: str, author: str = "Discord User"
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        url = f"https://api.github.com/repos/{self.repo}/issues/{pr_number}/comments"
        comment_body = f"{body}\n\n---\n**From:** `{author}` • *via Discord*"

        try:
            session = await self.get_session()
            async with session.post(
                url, json={"body": comment_body}, headers=await self._get_headers()
            ) as response:
                if response.status == 201:
                    return {
                        "success": True,
                        "pr_number": pr_number,
                        "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error adding comment: {e}")
            return {"error": str(e)}

    async def add_inline_comment(
        self,
        pr_number: int,
        body: str,
        path: str,
        line: int,
        side: str = "RIGHT",
        commit_id: Optional[str] = None,
        author: str = "Discord User",
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        if not commit_id:
            pr = await self.get_pr(pr_number)
            if pr.get("error"):
                return pr
            commit_id = pr["head"]["sha"]
            commits = await self.get_pr_commits(pr_number)
            if commits.get("commits"):
                commit_id = commits["commits"][-1]["sha"]

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/comments"
        payload = {
            "body": f"{body}\n\n---\n*via Discord (`{author}`)*",
            "path": path,
            "line": line,
            "side": side,
            "commit_id": commit_id,
        }

        try:
            session = await self.get_session()
            async with session.post(
                url, json=payload, headers=await self._get_headers()
            ) as response:
                if response.status == 201:
                    data = await response.json()
                    return {
                        "success": True,
                        "pr_number": pr_number,
                        "comment_id": data.get("id"),
                        "path": path,
                        "line": line,
                        "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                elif response.status == 422:
                    error_data = await response.json()
                    return {
                        "error": f"Invalid comment position: {error_data.get('message', 'validation failed')}"
                    }
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error adding inline comment: {e}")
            return {"error": str(e)}

    async def add_code_suggestion(
        self,
        pr_number: int,
        path: str,
        line: int,
        suggestion: str,
        message: str = "",
        side: str = "RIGHT",
        commit_id: Optional[str] = None,
        author: str = "Discord User",
    ) -> dict:
        body = f"{message}\n\n" if message else ""
        body += f"```suggestion\n{suggestion}\n```"
        body += f"\n\n---\n*Suggested via Discord (`{author}`)*"

        return await self.add_inline_comment(
            pr_number=pr_number,
            body=body,
            path=path,
            line=line,
            side=side,
            commit_id=commit_id,
            author=author,
        )

    async def get_file_at_ref(self, path: str, ref: str) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        import base64

        url = f"https://api.github.com/repos/{self.repo}/contents/{path}?ref={ref}"

        try:
            session = await self.get_session()
            async with session.get(url, headers=await self._get_headers()) as response:
                if response.status == 200:
                    data = await response.json()

                    content = ""
                    if data.get("encoding") == "base64" and data.get("content"):
                        try:
                            content = base64.b64decode(data["content"]).decode("utf-8")
                        except Exception:
                            content = "[Binary file or decoding error]"

                    return {
                        "path": path,
                        "ref": ref,
                        "content": content,
                        "size": data.get("size", 0),
                        "sha": data.get("sha", "")[:7],
                        "url": data.get("html_url", ""),
                    }
                elif response.status == 404:
                    return {
                        "error": f"File '{path}' not found at ref '{ref}'",
                        "not_found": True,
                    }
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error getting file at ref: {e}")
            return {"error": str(e)}

    async def get_pr_commits(self, pr_number: int) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/commits?per_page=100"

        try:
            session = await self.get_session()
            async with session.get(url, headers=await self._get_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    commits = [
                        {
                            "sha": c["sha"][:7],
                            "full_sha": c["sha"],
                            "message": c["commit"]["message"].split("\n")[0][:100],
                            "author": c["commit"]["author"]["name"],
                            "date": c["commit"]["author"]["date"][:10],
                            "url": c["html_url"],
                        }
                        for c in data
                    ]
                    return {
                        "pr_number": pr_number,
                        "commits": commits,
                        "count": len(commits),
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error getting PR commits: {e}")
            return {"error": str(e)}

    async def resolve_thread(self, thread_id: str) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        mutation = """
        mutation($threadId: ID!) {
            resolveReviewThread(input: {threadId: $threadId}) {
                thread { id isResolved }
            }
        }
        """

        try:
            result = await github_graphql._execute(mutation, {"threadId": thread_id})
            if result.get("error"):
                return {"error": result["error"]}

            return {"success": True, "thread_id": thread_id, "resolved": True}
        except Exception as e:
            logger.error(f"Error resolving thread: {e}")
            return {"error": str(e)}

    async def unresolve_thread(self, thread_id: str) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        mutation = """
        mutation($threadId: ID!) {
            unresolveReviewThread(input: {threadId: $threadId}) {
                thread { id isResolved }
            }
        }
        """

        try:
            result = await github_graphql._execute(mutation, {"threadId": thread_id})
            if result.get("error"):
                return {"error": result["error"]}

            return {"success": True, "thread_id": thread_id, "resolved": False}
        except Exception as e:
            logger.error(f"Error unresolving thread: {e}")
            return {"error": str(e)}

    async def get_review_threads(self, pr_number: int) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
            repository(owner: $owner, name: $repo) {
                pullRequest(number: $number) {
                    reviewThreads(first: 100) {
                        nodes {
                            id
                            isResolved
                            isOutdated
                            path
                            line
                            comments(first: 5) {
                                nodes {
                                    body
                                    author { login }
                                    createdAt
                                }
                            }
                        }
                    }
                }
            }
        }
        """

        try:
            result = await github_graphql._execute(
                query,
                {"owner": self.owner, "repo": self.repo_name, "number": pr_number},
            )

            if result.get("error"):
                return {"error": result["error"]}

            pr_data = result.get("data", {}).get("repository", {}).get("pullRequest")
            if not pr_data:
                return {"error": f"PR #{pr_number} not found", "not_found": True}

            threads = []
            for t in pr_data.get("reviewThreads", {}).get("nodes", []):
                comments = t.get("comments", {}).get("nodes", [])
                threads.append(
                    {
                        "id": t["id"],
                        "resolved": t["isResolved"],
                        "outdated": t["isOutdated"],
                        "path": t.get("path"),
                        "line": t.get("line"),
                        "comments_count": len(comments),
                        "first_comment": (comments[0]["body"] if comments else None),
                        "author": (
                            comments[0]["author"]["login"]
                            if comments and comments[0].get("author")
                            else "unknown"
                        ),
                    }
                )

            return {
                "pr_number": pr_number,
                "threads": threads,
                "total": len(threads),
                "resolved": sum(1 for t in threads if t["resolved"]),
                "unresolved": sum(1 for t in threads if not t["resolved"]),
            }
        except Exception as e:
            logger.error(f"Error getting review threads: {e}")
            return {"error": str(e)}

    async def enable_auto_merge(
        self, pr_number: int, merge_method: str = "SQUASH"
    ) -> dict:
        pr = await self.get_pr(pr_number)
        if pr.get("error"):
            return pr

        method_map = {"merge": "MERGE", "squash": "SQUASH", "rebase": "REBASE"}
        gql_method = method_map.get(merge_method.lower(), merge_method.upper())

        mutation = """
        mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
            enablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}) {
                pullRequest { number autoMergeRequest { enabledAt } }
            }
        }
        """

        try:
            result = await github_graphql._execute(
                mutation, {"pullRequestId": pr["node_id"], "mergeMethod": gql_method}
            )
            if result.get("error"):
                return {"error": result["error"]}

            return {
                "success": True,
                "pr_number": pr_number,
                "auto_merge": True,
                "merge_method": gql_method,
                "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
            }
        except Exception as e:
            logger.error(f"Error enabling auto-merge: {e}")
            return {"error": str(e)}

    async def disable_auto_merge(self, pr_number: int) -> dict:
        pr = await self.get_pr(pr_number)
        if pr.get("error"):
            return pr

        mutation = """
        mutation($pullRequestId: ID!) {
            disablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId}) {
                pullRequest { number }
            }
        }
        """

        try:
            result = await github_graphql._execute(
                mutation, {"pullRequestId": pr["node_id"]}
            )
            if result.get("error"):
                return {"error": result["error"]}

            return {
                "success": True,
                "pr_number": pr_number,
                "auto_merge": False,
                "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
            }
        except Exception as e:
            logger.error(f"Error disabling auto-merge: {e}")
            return {"error": str(e)}

    async def get_review_comments(self, pr_number: int) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/comments?per_page=100"

        try:
            session = await self.get_session()
            async with session.get(url, headers=await self._get_headers()) as response:
                if response.status == 200:
                    data = await response.json()
                    comments = [
                        {
                            "id": c["id"],
                            "path": c["path"],
                            "line": c.get("line") or c.get("original_line"),
                            "body": c["body"],
                            "author": (
                                c["user"]["login"] if c.get("user") else "unknown"
                            ),
                            "created_at": c["created_at"][:10],
                            "in_reply_to_id": c.get("in_reply_to_id"),
                            "url": c["html_url"],
                        }
                        for c in data
                    ]
                    return {
                        "pr_number": pr_number,
                        "comments": comments,
                        "count": len(comments),
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error getting review comments: {e}")
            return {"error": str(e)}

    async def remove_reviewer(
        self,
        pr_number: int,
        reviewers: Optional[list[str]] = None,
        team_reviewers: Optional[list[str]] = None,
    ) -> dict:
        if not self._has_auth():
            return {"error": "GitHub token not configured"}

        if not reviewers and not team_reviewers:
            return {"error": "At least one reviewer or team_reviewer required"}

        url = f"https://api.github.com/repos/{self.repo}/pulls/{pr_number}/requested_reviewers"
        payload = {}
        if reviewers:
            payload["reviewers"] = reviewers
        if team_reviewers:
            payload["team_reviewers"] = team_reviewers

        try:
            session = await self.get_session()
            async with session.delete(
                url, json=payload, headers=await self._get_headers()
            ) as response:
                if response.status == 200:
                    return {
                        "success": True,
                        "pr_number": pr_number,
                        "reviewers_removed": reviewers or [],
                        "teams_removed": team_reviewers or [],
                        "pr_url": f"https://github.com/{self.repo}/pull/{pr_number}",
                    }
                elif response.status == 404:
                    return {"error": f"PR #{pr_number} not found", "not_found": True}
                else:
                    return {"error": f"GitHub API error: {response.status}"}
        except Exception as e:
            logger.error(f"Error removing reviewers: {e}")
            return {"error": str(e)}

    async def review_pr(
        self, pr_number: int, post_to_github: bool = False, author: str = "Discord User"
    ) -> dict:
        from .pollinations import pollinations_client

        pr = await self.get_pr(pr_number)
        if pr.get("error"):
            return pr

        diff_result = await self.get_pr_diff(pr_number)
        if diff_result.get("error"):
            return diff_result

        diff = diff_result.get("diff", "")
        if not diff:
            return {"error": "No diff available for this PR"}

        formatted_diff = self._format_diff_for_review(diff)
        if not formatted_diff:
            return {"error": "No reviewable code files in this PR"}

        system_prompt = self._get_review_system_prompt()
        user_prompt = self._get_review_user_prompt(pr, formatted_diff)

        try:
            review_text = await pollinations_client.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model="gemini-large",
                temperature=0.3,
            )

            if not review_text:
                return {"error": "Failed to generate review"}

            review_text = self._parse_review(review_text)

            result = {
                "success": True,
                "pr_number": pr_number,
                "pr_title": pr["title"],
                "pr_url": pr["url"],
                "review": review_text,
                "posted_to_github": False,
            }

            if post_to_github:
                comment_result = await self.add_comment(
                    pr_number,
                    f"## AI Code Review\n\n{review_text}\n\n---\n*Requested by `{author}` via Discord*",
                    author,
                )
                if comment_result.get("success"):
                    result["posted_to_github"] = True

            return result

        except Exception as e:
            logger.error(f"Error generating PR review: {e}")
            return {"error": f"Failed to generate review: {str(e)}"}

    def _format_diff_for_review(self, diff_text: str) -> str:
        output_parts = []
        current_lines = []
        current_filename = None

        for line in diff_text.split("\n"):
            if line.startswith("diff --git"):
                if (
                    current_filename
                    and current_lines
                    and not self._should_skip_file(current_filename)
                ):
                    formatted = self._format_file_hunks(
                        current_filename, "\n".join(current_lines)
                    )
                    if formatted:
                        output_parts.append(formatted)

                current_lines = [line]
                match = re.match(r"diff --git a/(.*?) b/(.*)", line)
                current_filename = match.group(2) if match else "unknown"
            else:
                current_lines.append(line)

        if (
            current_filename
            and current_lines
            and not self._should_skip_file(current_filename)
        ):
            formatted = self._format_file_hunks(
                current_filename, "\n".join(current_lines)
            )
            if formatted:
                output_parts.append(formatted)

        return "\n".join(output_parts)

    def _format_file_hunks(self, filename: str, patch: str) -> str:
        lines = patch.split("\n")
        output = f"\n\n## File: '{filename}'\n"

        new_hunk_lines = []
        old_hunk_lines = []
        line_num = 0
        current_header = ""

        for line in lines:
            if line.startswith(("diff --git", "index ", "---", "+++")):
                continue

            if line.startswith("@@"):
                if new_hunk_lines or old_hunk_lines:
                    output += self._format_hunk(
                        current_header, new_hunk_lines, old_hunk_lines
                    )
                    new_hunk_lines = []
                    old_hunk_lines = []

                match = re.search(r"\+(\d+)", line)
                line_num = int(match.group(1)) - 1 if match else 0
                current_header = line

            elif line.startswith("+") and not line.startswith("+++"):
                line_num += 1
                new_hunk_lines.append(f"{line_num:4d} {line}")
            elif line.startswith("-") and not line.startswith("---"):
                old_hunk_lines.append(line)
            elif line.startswith(" ") or line == "":
                line_num += 1
                new_hunk_lines.append(f"{line_num:4d} {line}")

        if new_hunk_lines or old_hunk_lines:
            output += self._format_hunk(current_header, new_hunk_lines, old_hunk_lines)

        return output

    def _format_hunk(self, header: str, new_lines: list, old_lines: list) -> str:
        has_additions = any("+" in l for l in new_lines)
        has_deletions = bool(old_lines)

        if not has_additions and not has_deletions:
            return ""

        output = f"\n{header}\n__new hunk__\n"
        output += "\n".join(new_lines) + "\n"

        if has_deletions:
            output += "__old hunk__\n"
            output += "\n".join(old_lines) + "\n"

        return output

    def _should_skip_file(self, filename: str) -> bool:
        for pattern in SKIP_FILE_PATTERNS:
            if pattern.search(filename):
                return True
        return False

    def _get_review_system_prompt(self) -> str:
        return """You are a code reviewer analyzing a Pull Request.

DIFF FORMAT: __new hunk__ = new code with line numbers, __old hunk__ = removed code

Review for:
1. **Bugs** - Logic errors, edge cases, null checks
2. **Security** - Injection, XSS, auth issues, secrets in code
3. **Performance** - N+1 queries, memory leaks, inefficient loops

Skip style/formatting nitpicks. Focus on issues that matter.

OUTPUT FORMAT:
- If no major issues: Start with "**LGTM** - No major issues found." then optionally list minor suggestions
- If issues found: List each issue with file:line reference and brief explanation

Keep your review concise (200-500 words). Be direct and actionable."""

    def _get_review_user_prompt(self, pr: dict, diff: str) -> str:
        return f"""**PR #{pr['number']}:** {pr['title']}

**Author:** {pr['author']}
**Changes:** +{pr['additions']} -{pr['deletions']} across {pr['changed_files']} files

**Description:**
{pr.get('body', 'No description provided')}

**Code Diff:**
{diff}

Review this PR for bugs, security issues, and performance problems. Be concise."""

    def _parse_review(self, response: str) -> str:
        review = response.strip()

        if review.startswith("```"):
            lines = review.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            review = "\n".join(lines)

        return review.strip()


github_pr_manager = GitHubPRManager()


async def tool_github_pr(
    action: str,
    pr_number: int = None,
    state: str = "open",
    limit: int = 10,
    base: str = None,
    title: str = None,
    body: str = None,
    head: str = None,
    draft: bool = False,
    reviewers: list[str] = None,
    team_reviewers: list[str] = None,
    event: str = None,
    commit_title: str = None,
    commit_message: str = None,
    merge_method: str = "merge",
    comment: str = None,
    post_review_to_github: bool = False,
    path: str = None,
    line: int = None,
    side: str = "RIGHT",
    suggestion: str = None,
    thread_id: str = None,
    file_path: str = None,
    ref: str = "main",
    edit_index: int = None,
    reporter: str = "Discord User",
    _context: dict = None,
    **kwargs,
) -> dict:
    is_admin = False
    context_user_id = None
    context_user_name = None
    if _context:
        is_admin = _context.get("is_admin", False)
        context_user_id = _context.get("user_id")
        context_user_name = _context.get("user_name")
        reporter = _context.get("reporter", reporter)

    action = action.lower()

    ADMIN_ACTIONS = {
        "request_review",
        "remove_reviewer",
        "approve",
        "request_changes",
        "merge",
        "update",
        "create",
        "convert_to_draft",
        "ready_for_review",
        "update_branch",
        "inline_comment",
        "suggest",
        "resolve_thread",
        "unresolve_thread",
        "enable_auto_merge",
        "disable_auto_merge",
        "close",
        "reopen",
    }
    if action in ADMIN_ACTIONS:
        if not is_admin:
            logger.warning(
                f"SECURITY: Blocked PR admin action '{action}' for non-admin user {context_user_name} (id={context_user_id})"
            )
            return {
                "error": f"The '{action}' action requires admin permissions. Ask a team member with admin access!"
            }
        else:
            logger.info(
                f"PR admin action '{action}' authorized for {context_user_name} (id={context_user_id})"
            )

    if action == "get":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.get_pr(pr_number)

    elif action == "list":
        return await github_pr_manager.list_prs(state=state, limit=limit, base=base)

    elif action == "get_history":
        if not pr_number:
            return {"error": "pr_number required for 'get_history' action"}
        from .github_graphql import github_graphql

        history = await github_graphql.get_edit_history(
            number=pr_number, is_pr=True, limit=limit or 10, edit_index=edit_index
        )
        return history

    elif action == "get_files":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.get_pr_files(pr_number)

    elif action == "get_diff":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.get_pr_diff(pr_number)

    elif action == "get_checks":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.get_pr_checks(pr_number)

    elif action == "get_file_at_ref":
        if not file_path:
            return {
                "error": "file_path required (e.g., file_path='.github/workflows/ci.yml', ref='main' or branch name)"
            }
        return await github_pr_manager.get_file_at_ref(file_path, ref)

    elif action == "request_review":
        if not pr_number:
            return {"error": "pr_number required"}
        if not reviewers and not team_reviewers:
            return {"error": "reviewers or team_reviewers required"}
        return await github_pr_manager.request_reviewers(
            pr_number, reviewers, team_reviewers
        )

    elif action == "approve":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.create_review(pr_number, "APPROVE", body)

    elif action == "request_changes":
        if not pr_number or not body:
            return {"error": "pr_number and body required"}
        return await github_pr_manager.create_review(pr_number, "REQUEST_CHANGES", body)

    elif action == "merge":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.merge_pr(
            pr_number, commit_title, commit_message, merge_method
        )

    elif action == "update":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.update_pr(pr_number, title, body, state, base)

    elif action == "close":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.update_pr(pr_number, state="closed")

    elif action == "reopen":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.update_pr(pr_number, state="open")

    elif action == "create":
        if not title or not head:
            return {"error": "title and head (branch) required"}
        return await github_pr_manager.create_pr(
            title, body or "", head, base or "main", draft
        )

    elif action == "convert_to_draft":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.convert_to_draft(pr_number)

    elif action == "ready_for_review":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.mark_ready_for_review(pr_number)

    elif action == "update_branch":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.update_branch(pr_number)

    elif action == "comment":
        if not pr_number or not comment:
            return {"error": "pr_number and comment required"}
        return await github_pr_manager.add_comment(pr_number, comment, reporter)

    elif action == "get_commits":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.get_pr_commits(pr_number)

    elif action == "inline_comment":
        if not pr_number or not path or not line or not comment:
            return {"error": "pr_number, path, line, and comment required"}
        return await github_pr_manager.add_inline_comment(
            pr_number, comment, path, line, side, author=reporter
        )

    elif action == "suggest":
        if not pr_number or not path or not line or not suggestion:
            return {"error": "pr_number, path, line, and suggestion required"}
        return await github_pr_manager.add_code_suggestion(
            pr_number, path, line, suggestion, comment or "", side, author=reporter
        )

    elif action == "get_threads":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.get_review_threads(pr_number)

    elif action == "resolve_thread":
        if not thread_id:
            return {"error": "thread_id required"}
        return await github_pr_manager.resolve_thread(thread_id)

    elif action == "unresolve_thread":
        if not thread_id:
            return {"error": "thread_id required"}
        return await github_pr_manager.unresolve_thread(thread_id)

    elif action == "get_review_comments":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.get_review_comments(pr_number)

    elif action == "remove_reviewer":
        if not pr_number:
            return {"error": "pr_number required"}
        if not reviewers and not team_reviewers:
            return {"error": "reviewers or team_reviewers required"}
        return await github_pr_manager.remove_reviewer(
            pr_number, reviewers, team_reviewers
        )

    elif action == "enable_auto_merge":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.enable_auto_merge(pr_number, merge_method)

    elif action == "disable_auto_merge":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.disable_auto_merge(pr_number)

    elif action == "review":
        if not pr_number:
            return {"error": "pr_number required"}
        return await github_pr_manager.review_pr(
            pr_number, post_review_to_github, reporter
        )

    else:
        return {
            "error": f"Unknown action: {action}",
            "valid_actions": [
                "get",
                "list",
                "get_files",
                "get_diff",
                "get_checks",
                "get_commits",
                "get_threads",
                "get_review_comments",
                "get_file_at_ref",
                "request_review",
                "remove_reviewer",
                "approve",
                "request_changes",
                "merge",
                "update",
                "close",
                "reopen",
                "create",
                "convert_to_draft",
                "ready_for_review",
                "update_branch",
                "comment",
                "inline_comment",
                "suggest",
                "resolve_thread",
                "unresolve_thread",
                "enable_auto_merge",
                "disable_auto_merge",
                "review",
            ],
        }

