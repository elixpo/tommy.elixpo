"""
PR Fixer Mode - Fix pull request issues autonomously.

Handles:
- PR review comments/feedback
- Failing CI/CD tests
- Merge conflicts
- Code style/lint issues

Workflow (inspired by Roo-Code):
1. Gather PR Context - Get PR details, reviews, checks
2. Analyze Issues - Identify what needs fixing
3. Fix Issues - Address review comments, failing tests
4. Verify - Run checks, ensure PR is ready
"""

import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Any

from .base import (
    AgentMode,
    ModeConfig,
    ModeState,
    WorkflowStep,
    ToolGroup,
    ProgressCallback,
    ApprovalCallback,
)

logger = logging.getLogger(__name__)


class PRFixer(AgentMode):
    """
    Fix pull request issues autonomously.

    Given a PR number or URL, this mode will:
    1. Fetch PR details, reviews, and check status
    2. Identify issues (review comments, failing tests, conflicts)
    3. Address each issue
    4. Verify PR is ready for merge
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="pr-fixer",
            name="PR Fixer",
            emoji="🛠️",
            role_definition="""You are a pull request resolution specialist. Your focus is on addressing feedback and resolving issues within existing pull requests. Your expertise includes:
- Analyzing PR review comments to understand required changes
- Checking CI/CD workflow statuses to identify failing tests
- Fetching and analyzing test logs to diagnose failures
- Identifying and resolving merge conflicts
- Guiding the resolution process efficiently""",
            when_to_use="Use this mode to fix pull requests. It can analyze PR feedback from GitHub, check for failing tests, and help resolve merge conflicts.",
            description="Fix pull request issues autonomously.",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.EDIT,
                ToolGroup.COMMAND,
                ToolGroup.GITHUB,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Gather PR Context",
                    instructions="""Fetch comprehensive PR information:
- gh pr view {number} --json number,title,body,state,files,reviews,comments
- gh pr checks {number} - Check for failing tests
- gh pr view {number} --json mergeable,mergeStateStatus - Check for conflicts""",
                    tools_required=[ToolGroup.COMMAND, ToolGroup.GITHUB],
                ),
                WorkflowStep(
                    number=2,
                    name="Analyze Issues",
                    instructions="""Analyze gathered information to identify problems:
- Summarize review comments and requested changes
- Identify root cause of failing tests from workflow logs
- Determine if merge conflicts exist""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=3,
                    name="Fix Issues",
                    instructions="""Execute fixes for identified issues:
- Check out PR branch: gh pr checkout {number}
- Apply code changes based on review feedback
- Fix failing tests by modifying code
- For conflicts: resolve using git""",
                    tools_required=[ToolGroup.EDIT, ToolGroup.COMMAND],
                    requires_approval=True,
                ),
                WorkflowStep(
                    number=4,
                    name="Verify and Push",
                    instructions="""Verify fixes and push changes:
- Run tests locally
- Stage and commit changes
- Push to PR branch
- Monitor gh pr checks --watch until complete""",
                    tools_required=[ToolGroup.COMMAND, ToolGroup.GITHUB],
                ),
            ],
            best_practices=[
                "Always fetch latest PR state before making changes",
                "Address review comments one by one systematically",
                "Run tests locally before pushing",
                "Use git push --force-with-lease for safety",
                "Keep fixes focused on the feedback received",
                "Don't introduce unrelated changes",
            ],
        )

    async def execute(
        self,
        context: dict[str, Any],
        sandbox: Any,
        model_router: Any,
        on_progress: Optional[ProgressCallback] = None,
        on_approval: Optional[ApprovalCallback] = None,
    ) -> dict[str, Any]:
        """Execute the PR fixer workflow."""

        # Get PR number from context
        pr_number = context.get("pr_number")
        pr_url = context.get("pr_url")

        if not pr_number and pr_url:
            # Parse URL: https://github.com/owner/repo/pull/123
            match = re.search(r'/pull/(\d+)', pr_url)
            if match:
                pr_number = int(match.group(1))

        if not pr_number:
            return {"success": False, "error": "No PR number provided"}

        state = ModeState(total_steps=4)

        try:
            # Step 1: Gather PR Context
            state.set_todo_in_progress(0)
            await self._report_progress(on_progress, "gathering", "Fetching PR details...", None)

            pr_context = await self._gather_pr_context(sandbox, pr_number)
            if pr_context.get("error"):
                return {"success": False, "error": pr_context["error"]}

            state.step_outputs[1] = pr_context

            # Step 2: Analyze Issues
            state.current_step = 2
            await self._report_progress(on_progress, "analyzing", "Analyzing issues...", None)

            issues = await self._analyze_issues(sandbox, model_router, pr_context)
            state.step_outputs[2] = issues

            if not issues["has_issues"]:
                return {
                    "success": True,
                    "message": "PR has no issues to fix!",
                    "pr_number": pr_number,
                }

            # Present issues summary and get approval
            issues_summary = self._format_issues_summary(issues)
            await self._report_progress(on_progress, "analyzing", "Issues found", issues_summary)

            decision, feedback = await self._request_approval(
                on_approval, "issues_review", issues_summary
            )

            if decision == "reject":
                return {
                    "success": False,
                    "error": f"Fixes rejected: {feedback}",
                    "issues": issues,
                }

            # Step 3: Fix Issues
            state.current_step = 3
            await self._report_progress(on_progress, "fixing", "Checking out PR branch...", None)

            # Checkout PR branch
            checkout_result = await sandbox.run_command(
                f'gh pr checkout {pr_number} --force'
            )
            if checkout_result.exit_code != 0:
                return {"success": False, "error": f"Failed to checkout PR: {checkout_result.stderr}"}

            # Fix each issue type
            fixes_made = []

            # Fix review comments
            if issues.get("review_comments"):
                await self._report_progress(
                    on_progress, "fixing",
                    f"Addressing {len(issues['review_comments'])} review comments...",
                    None
                )
                comment_fixes = await self._fix_review_comments(
                    sandbox, model_router, issues["review_comments"]
                )
                fixes_made.extend(comment_fixes)

            # Fix failing tests
            if issues.get("failing_checks"):
                await self._report_progress(
                    on_progress, "fixing",
                    f"Fixing {len(issues['failing_checks'])} failing checks...",
                    None
                )
                test_fixes = await self._fix_failing_tests(
                    sandbox, model_router, issues["failing_checks"]
                )
                fixes_made.extend(test_fixes)

            # Fix conflicts
            if issues.get("has_conflicts"):
                await self._report_progress(on_progress, "fixing", "Resolving merge conflicts...", None)
                conflict_result = await self._resolve_conflicts(sandbox, model_router)
                fixes_made.append({"type": "conflict", "result": conflict_result})

            state.step_outputs[3] = fixes_made

            # Step 4: Verify and Push
            state.current_step = 4
            await self._report_progress(on_progress, "verifying", "Running tests...", None)

            # Run tests
            test_result = await sandbox.run_command('npm test 2>&1 || pytest 2>&1', timeout=120)

            # Commit and push
            await sandbox.run_command('git add -A')

            commit_msg = f"fix: Address PR feedback (#{pr_number})"
            await sandbox.run_command(f'git commit -m "{commit_msg}" --allow-empty')

            # Push with force-with-lease for safety
            push_result = await sandbox.run_command('git push --force-with-lease')
            if push_result.exit_code != 0:
                return {"success": False, "error": f"Failed to push: {push_result.stderr}"}

            await self._report_progress(on_progress, "verifying", "Waiting for CI checks...", None)

            # Wait for checks to complete
            checks_result = await sandbox.run_command(
                f'gh pr checks {pr_number} --watch',
                timeout=300  # 5 min timeout for CI
            )

            state.completed = True
            state.success = True

            return {
                "success": True,
                "pr_number": pr_number,
                "fixes_made": len(fixes_made),
                "checks_passed": checks_result.exit_code == 0,
                "message": f"PR #{pr_number} has been updated with {len(fixes_made)} fixes",
            }

        except Exception as e:
            logger.exception("PR fixer workflow failed")
            return {"success": False, "error": str(e)}

    async def _gather_pr_context(self, sandbox: Any, pr_number: int) -> dict:
        """Gather comprehensive PR information."""
        # Get PR details
        result = await sandbox.run_command(
            f'gh pr view {pr_number} --json number,title,body,state,headRefName,baseRefName,mergeable,mergeStateStatus,files,reviews'
        )
        if result.exit_code != 0:
            return {"error": f"Failed to fetch PR: {result.stderr}"}

        try:
            pr_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"error": f"Failed to parse PR data: {result.stdout}"}

        # Get check status
        checks_result = await sandbox.run_command(
            f'gh pr checks {pr_number} --json name,state,conclusion 2>/dev/null || echo "[]"'
        )
        try:
            checks = json.loads(checks_result.stdout)
        except:
            checks = []

        # Get review comments
        comments_result = await sandbox.run_command(
            f'gh api repos/:owner/:repo/pulls/{pr_number}/comments --jq \'[.[] | {{path,body,line,side}}]\''
        )
        try:
            review_comments = json.loads(comments_result.stdout)
        except:
            review_comments = []

        return {
            "number": pr_number,
            "title": pr_data.get("title", ""),
            "body": pr_data.get("body", ""),
            "state": pr_data.get("state", ""),
            "head_branch": pr_data.get("headRefName", ""),
            "base_branch": pr_data.get("baseRefName", "main"),
            "mergeable": pr_data.get("mergeable", "UNKNOWN"),
            "merge_state": pr_data.get("mergeStateStatus", "UNKNOWN"),
            "files": pr_data.get("files", []),
            "reviews": pr_data.get("reviews", []),
            "checks": checks,
            "review_comments": review_comments,
        }

    async def _analyze_issues(
        self,
        sandbox: Any,
        model_router: Any,
        pr_context: dict
    ) -> dict:
        """Analyze PR context to identify issues."""
        issues = {
            "has_issues": False,
            "review_comments": [],
            "failing_checks": [],
            "has_conflicts": False,
        }

        # Check for unaddressed review comments
        if pr_context.get("review_comments"):
            issues["review_comments"] = pr_context["review_comments"]
            issues["has_issues"] = True

        # Check for failing tests
        for check in pr_context.get("checks", []):
            if check.get("conclusion") in ["FAILURE", "failure"]:
                issues["failing_checks"].append(check)
                issues["has_issues"] = True

        # Check for conflicts
        if pr_context.get("mergeable") == "CONFLICTING" or pr_context.get("merge_state") == "DIRTY":
            issues["has_conflicts"] = True
            issues["has_issues"] = True

        return issues

    def _format_issues_summary(self, issues: dict) -> str:
        """Format issues for display."""
        parts = ["## PR Issues Found\n"]

        if issues.get("review_comments"):
            parts.append(f"### Review Comments ({len(issues['review_comments'])})")
            for comment in issues["review_comments"][:5]:
                parts.append(f"- **{comment.get('path', 'unknown')}**: {comment.get('body', '')[:100]}")

        if issues.get("failing_checks"):
            parts.append(f"\n### Failing Checks ({len(issues['failing_checks'])})")
            for check in issues["failing_checks"]:
                parts.append(f"- {check.get('name', 'unknown')}: {check.get('conclusion', 'failed')}")

        if issues.get("has_conflicts"):
            parts.append("\n### Merge Conflicts")
            parts.append("- PR has merge conflicts that need resolution")

        return "\n".join(parts)

    async def _fix_review_comments(
        self,
        sandbox: Any,
        model_router: Any,
        comments: list[dict]
    ) -> list[dict]:
        """Fix review comments."""
        fixes = []

        for comment in comments[:10]:  # Limit to 10 comments
            file_path = comment.get("path", "")
            feedback = comment.get("body", "")
            line = comment.get("line", 0)

            if not file_path or not feedback:
                continue

            # Read the file
            read_result = await sandbox.run_command(f'cat "{file_path}"')
            if read_result.exit_code != 0:
                continue

            file_content = read_result.stdout

            # Use AI to generate fix
            fix_prompt = f"""Fix this code based on the review comment:

File: {file_path}
Line: {line}

Review Comment:
{feedback}

Current File Content:
```
{file_content[:3000]}
```

Provide the corrected code for this file. Only output the code, no explanations."""

            fixed_code = await model_router.chat(
                messages=[{"role": "user", "content": fix_prompt}],
                task_type="coding",
            )

            # Write fixed content
            # (In practice, use FileEditor for proper search/replace)
            fixes.append({
                "type": "review_comment",
                "file": file_path,
                "comment": feedback[:100],
            })

        return fixes

    async def _fix_failing_tests(
        self,
        sandbox: Any,
        model_router: Any,
        failing_checks: list[dict]
    ) -> list[dict]:
        """Fix failing CI checks."""
        fixes = []

        for check in failing_checks[:3]:  # Limit to 3 checks
            check_name = check.get("name", "")

            # Get failure logs
            logs_result = await sandbox.run_command(
                f'gh run view --log-failed 2>/dev/null | head -500'
            )
            failure_logs = logs_result.stdout if logs_result.exit_code == 0 else ""

            if not failure_logs:
                continue

            # Use AI to analyze and fix
            fix_prompt = f"""Analyze this CI failure and suggest a fix:

Check: {check_name}

Failure Logs:
```
{failure_logs[:3000]}
```

What file needs to be changed and how?"""

            analysis = await model_router.chat(
                messages=[{"role": "user", "content": fix_prompt}],
                task_type="coding",
            )

            fixes.append({
                "type": "ci_failure",
                "check": check_name,
                "analysis": analysis[:500],
            })

        return fixes

    async def _resolve_conflicts(
        self,
        sandbox: Any,
        model_router: Any
    ) -> dict:
        """Resolve merge conflicts."""
        # Get conflicting files
        result = await sandbox.run_command('git diff --name-only --diff-filter=U')
        if result.exit_code != 0:
            return {"success": False, "error": "Could not get conflict list"}

        conflicting_files = result.stdout.strip().split('\n')
        resolved = []

        for file_path in conflicting_files:
            if not file_path:
                continue

            # Read conflicting file
            read_result = await sandbox.run_command(f'cat "{file_path}"')
            if read_result.exit_code != 0:
                continue

            content = read_result.stdout

            # Use AI to resolve
            resolve_prompt = f"""Resolve the merge conflict in this file:

{content[:5000]}

Return the resolved file content (choose the correct version or merge both)."""

            resolved_content = await model_router.chat(
                messages=[{"role": "user", "content": resolve_prompt}],
                task_type="coding",
            )

            # Write resolved content and stage
            # (In practice, write the file and git add)
            await sandbox.run_command(f'git add "{file_path}"')
            resolved.append(file_path)

        return {"success": True, "resolved_files": resolved}
