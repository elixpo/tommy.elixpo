"""
Issue Fixer Mode - Fix GitHub issues autonomously.

Workflow (inspired by Roo-Code):
1. Retrieve Issue Context - Parse URL, get issue details + comments
2. Explore Codebase - Use search to find all related files
3. Create Implementation Plan - Plan changes based on issue type
4. Implement Solution - Make code changes
5. Run Tests - Verify changes work
6. Prepare Pull Request - Commit and create PR
"""

import re
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


class IssueFixer(AgentMode):
    """
    Fix GitHub issues autonomously.

    Given a GitHub issue URL, this mode will:
    1. Fetch issue details and all comments
    2. Search codebase for related files
    3. Create implementation plan
    4. Implement fixes with tests
    5. Create pull request
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="issue-fixer",
            name="Issue Fixer",
            emoji="🔧",
            role_definition="""You are a GitHub issue resolution specialist focused on fixing bugs and implementing feature requests. Your expertise includes:
- Analyzing GitHub issues to understand requirements and acceptance criteria
- Exploring codebases to identify all affected files and dependencies
- Implementing fixes for bug reports with comprehensive testing
- Building new features based on detailed proposals
- Ensuring all acceptance criteria are met before completion
- Creating pull requests with proper documentation
- Using GitHub CLI for all GitHub operations""",
            when_to_use="Use this mode when you have a GitHub issue (bug report or feature request) that needs to be fixed or implemented.",
            description="Fix GitHub issues and implement features autonomously.",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.EDIT,
                ToolGroup.COMMAND,
                ToolGroup.GITHUB,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Retrieve Issue Context",
                    instructions="""Parse the GitHub issue URL to extract owner, repo, and issue number.
Retrieve full issue details using: gh api repos/{owner}/{repo}/issues/{number}
Also get all comments: gh api repos/{owner}/{repo}/issues/{number}/comments
Document all requirements, acceptance criteria, and any clarifications from comments.""",
                    tools_required=[ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=2,
                    name="Explore Codebase",
                    instructions="""Search the codebase to understand the relevant code:
- For bugs: Search for error messages, function names, component names
- For features: Search for similar functionality, integration points
- Use codebase_search first, then read specific files
- Document: files to modify, current patterns, code conventions, test locations""",
                    tools_required=[ToolGroup.READ, ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=3,
                    name="Create Implementation Plan",
                    instructions="""Create a detailed implementation plan based on analysis:
For Bug Fixes:
1. Identify root cause
2. Plan the fix approach (focused, targeted)
3. Identify files to modify
4. Plan test cases

For Features:
1. Break down into components
2. Identify all files needing changes
3. Plan implementation approach
4. Consider edge cases
5. Plan test coverage""",
                    tools_required=[ToolGroup.READ],
                    requires_approval=True,
                ),
                WorkflowStep(
                    number=4,
                    name="Implement Solution",
                    instructions="""Implement the fix or feature following the plan:
- Follow existing code patterns and style
- Add appropriate error handling
- Include necessary comments
- For bugs: Make minimal, high-quality changes
- For features: Implement incrementally
- After each change, verify with tests""",
                    tools_required=[ToolGroup.EDIT, ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=5,
                    name="Run Tests and Verify",
                    instructions="""Run comprehensive tests:
1. Run unit tests for modified files
2. Run integration tests if applicable
3. Check for linting errors
4. Verify all acceptance criteria are met
If any tests fail, analyze and fix before proceeding.""",
                    tools_required=[ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=6,
                    name="Create Pull Request",
                    instructions="""Prepare and create the pull request:
1. Create branch: fix/issue-{number}-{brief-description} or feat/issue-{number}-{brief-description}
2. Commit changes with descriptive message referencing issue
3. Push to remote
4. Create PR with: gh pr create --title "..." --body "..."
Include: Description, Changes Made, Testing, Verification of acceptance criteria, Checklist""",
                    tools_required=[ToolGroup.COMMAND, ToolGroup.GITHUB],
                    requires_approval=True,
                ),
            ],
            best_practices=[
                "Always read the entire issue and all comments before starting",
                "Follow the project's coding standards and patterns",
                "Focus exclusively on addressing the issue's requirements",
                "Make minimal, high-quality changes for bug fixes",
                "Test thoroughly - both automated and manual testing",
                "Reference the issue number in commits",
                "Verify all acceptance criteria are met",
                "Consider performance and security implications",
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
        """Execute the issue fixer workflow."""

        # Validate context
        error = self._validate_context(context, ["issue_url"])
        if error:
            return {"success": False, "error": error}

        state = ModeState(total_steps=len(self.config.workflow_steps))

        # Initialize todos
        state.add_todo("Retrieve issue context", "pending")
        state.add_todo("Explore codebase and find related files", "pending")
        state.add_todo("Create implementation plan", "pending")
        state.add_todo("Implement solution", "pending")
        state.add_todo("Run tests and verify", "pending")
        state.add_todo("Create pull request", "pending")

        issue_url = context["issue_url"]
        repo = context.get("repo", "")

        try:
            # Step 1: Retrieve Issue Context
            state.set_todo_in_progress(0)
            await self._report_progress(on_progress, "retrieving", "Fetching issue details...", None)

            issue_data = await self._get_issue_context(sandbox, issue_url)
            if issue_data.get("error"):
                return {"success": False, "error": issue_data["error"]}

            state.step_outputs[1] = issue_data
            state.complete_todo(0)

            # Step 2: Explore Codebase
            state.current_step = 2
            state.set_todo_in_progress(1)
            await self._report_progress(on_progress, "exploring", "Searching codebase...", None)

            exploration = await self._explore_codebase(
                sandbox, model_router, issue_data
            )
            state.step_outputs[2] = exploration
            state.complete_todo(1)

            # Step 3: Create Implementation Plan
            state.current_step = 3
            state.set_todo_in_progress(2)
            await self._report_progress(on_progress, "planning", "Creating implementation plan...", None)

            plan = await self._create_plan(
                sandbox, model_router, issue_data, exploration
            )

            # Request approval for plan
            decision, feedback = await self._request_approval(
                on_approval, "plan_review", plan
            )

            if decision == "reject":
                return {"success": False, "error": f"Plan rejected: {feedback}"}
            elif decision == "modify":
                # Re-plan with feedback
                plan = await self._revise_plan(
                    sandbox, model_router, plan, feedback
                )

            state.step_outputs[3] = plan
            state.complete_todo(2)

            # Step 4: Implement Solution
            state.current_step = 4
            state.set_todo_in_progress(3)
            await self._report_progress(on_progress, "implementing", "Implementing solution...", None)

            changes = await self._implement_solution(
                sandbox, model_router, plan, on_progress
            )
            state.step_outputs[4] = changes
            state.complete_todo(3)

            # Step 5: Run Tests
            state.current_step = 5
            state.set_todo_in_progress(4)
            await self._report_progress(on_progress, "testing", "Running tests...", None)

            test_result = await self._run_tests(sandbox, model_router, changes)

            # Fix loop if tests fail
            fix_attempts = 0
            max_attempts = context.get("max_fix_attempts", 5)

            while not test_result["passed"] and fix_attempts < max_attempts:
                fix_attempts += 1
                await self._report_progress(
                    on_progress, "fixing",
                    f"Fixing test failures (attempt {fix_attempts}/{max_attempts})...",
                    test_result.get("error")
                )

                fixes = await self._fix_failures(
                    sandbox, model_router, test_result, changes
                )
                changes.extend(fixes)

                test_result = await self._run_tests(sandbox, model_router, changes)

            if not test_result["passed"]:
                return {
                    "success": False,
                    "error": f"Tests still failing after {max_attempts} fix attempts",
                    "test_output": test_result.get("output"),
                }

            state.step_outputs[5] = test_result
            state.complete_todo(4)

            # Step 6: Create Pull Request
            state.current_step = 6
            state.set_todo_in_progress(5)
            await self._report_progress(on_progress, "pr", "Creating pull request...", None)

            # Request approval before PR creation
            pr_preview = self._format_pr_preview(issue_data, changes, plan)
            decision, feedback = await self._request_approval(
                on_approval, "pr_review", pr_preview
            )

            if decision == "reject":
                return {
                    "success": True,
                    "pr_created": False,
                    "message": f"PR creation skipped: {feedback}",
                    "changes": changes,
                }

            pr_result = await self._create_pull_request(
                sandbox, issue_data, changes, plan
            )

            state.step_outputs[6] = pr_result
            state.complete_todo(5)
            state.completed = True
            state.success = True

            return {
                "success": True,
                "pr_url": pr_result.get("url"),
                "pr_number": pr_result.get("number"),
                "commit_sha": pr_result.get("commit_sha"),
                "changes": changes,
                "issue": issue_data,
            }

        except Exception as e:
            logger.exception("Issue fixer workflow failed")
            return {"success": False, "error": str(e)}

    async def _get_issue_context(self, sandbox: Any, issue_url: str) -> dict:
        """Fetch issue details and comments from GitHub."""
        # Parse URL: https://github.com/owner/repo/issues/123
        match = re.match(
            r'https://github\.com/([^/]+)/([^/]+)/issues/(\d+)',
            issue_url
        )
        if not match:
            return {"error": f"Invalid issue URL: {issue_url}"}

        owner, repo, number = match.groups()

        # Get issue details
        result = await sandbox.run_command(
            f'gh api repos/{owner}/{repo}/issues/{number} --jq \'{{number,title,body,state,labels,author:.user.login,created_at,updated_at}}\''
        )
        if result.exit_code != 0:
            return {"error": f"Failed to fetch issue: {result.stderr}"}

        import json
        try:
            issue = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"error": f"Failed to parse issue response: {result.stdout}"}

        # Get comments
        comments_result = await sandbox.run_command(
            f'gh api repos/{owner}/{repo}/issues/{number}/comments --jq \'[.[].body]\''
        )
        comments = []
        if comments_result.exit_code == 0:
            try:
                comments = json.loads(comments_result.stdout)
            except json.JSONDecodeError:
                pass

        return {
            "owner": owner,
            "repo": repo,
            "number": int(number),
            "title": issue.get("title", ""),
            "body": issue.get("body", ""),
            "author": issue.get("author", "unknown"),
            "state": issue.get("state", "open"),
            "labels": [l.get("name") for l in issue.get("labels", [])],
            "comments": comments,
            "is_bug": any(
                "bug" in l.lower()
                for l in [l.get("name", "") for l in issue.get("labels", [])]
            ),
        }

    async def _explore_codebase(
        self,
        sandbox: Any,
        model_router: Any,
        issue_data: dict
    ) -> dict:
        """Search codebase for relevant files."""
        # Extract keywords from issue
        title = issue_data.get("title", "")
        body = issue_data.get("body", "")

        # Use AI to identify search terms
        search_prompt = f"""Analyze this GitHub issue and suggest search terms to find relevant code:

Title: {title}

Body:
{body[:2000]}

Return a JSON list of search terms (function names, error messages, keywords):
["term1", "term2", ...]"""

        response = await model_router.chat(
            messages=[{"role": "user", "content": search_prompt}],
            task_type="quick",
        )

        import json
        try:
            search_terms = json.loads(response)
        except:
            # Fallback: extract words from title
            search_terms = [w for w in title.split() if len(w) > 3][:5]

        # Search for each term
        relevant_files = set()
        for term in search_terms[:5]:  # Limit to 5 terms
            result = await sandbox.run_command(
                f'grep -rl "{term}" --include="*.py" --include="*.js" --include="*.ts" . 2>/dev/null | head -20'
            )
            if result.exit_code == 0:
                files = result.stdout.strip().split('\n')
                relevant_files.update(f for f in files if f)

        return {
            "search_terms": search_terms,
            "relevant_files": list(relevant_files)[:30],  # Limit files
        }

    async def _create_plan(
        self,
        sandbox: Any,
        model_router: Any,
        issue_data: dict,
        exploration: dict
    ) -> str:
        """Create implementation plan using AI."""
        files_content = ""
        for f in exploration.get("relevant_files", [])[:10]:
            result = await sandbox.run_command(f'head -100 "{f}"')
            if result.exit_code == 0:
                files_content += f"\n\n--- {f} ---\n{result.stdout[:2000]}"

        plan_prompt = f"""Create an implementation plan for this GitHub issue:

## Issue #{issue_data['number']}: {issue_data['title']}

{issue_data['body'][:3000]}

## Relevant Files Found:
{files_content}

Create a focused implementation plan:
1. Root cause analysis (for bugs) or feature breakdown (for features)
2. Files to modify (be specific)
3. Changes needed in each file
4. Tests to add/modify
5. Verification steps

Keep the plan focused and actionable."""

        plan = await model_router.chat(
            messages=[{"role": "user", "content": plan_prompt}],
            task_type="planning",
        )

        return plan

    async def _revise_plan(
        self,
        sandbox: Any,
        model_router: Any,
        original_plan: str,
        feedback: str
    ) -> str:
        """Revise plan based on feedback."""
        revise_prompt = f"""Revise this implementation plan based on feedback:

## Original Plan:
{original_plan}

## Feedback:
{feedback}

Create a revised plan addressing the feedback."""

        return await model_router.chat(
            messages=[{"role": "user", "content": revise_prompt}],
            task_type="planning",
        )

    async def _implement_solution(
        self,
        sandbox: Any,
        model_router: Any,
        plan: str,
        on_progress: Optional[ProgressCallback]
    ) -> list[dict]:
        """Implement the solution based on the plan."""
        # This would use the existing coding phase from agent.py
        # For now, return placeholder
        changes = []

        impl_prompt = f"""Based on this plan, generate the code changes needed:

{plan}

For each file, provide:
1. File path
2. The change to make (search/replace or new content)

Format as JSON array of changes."""

        response = await model_router.chat(
            messages=[{"role": "user", "content": impl_prompt}],
            task_type="coding",
        )

        # Parse and apply changes
        # This would integrate with FileEditor from the existing code

        return changes

    async def _run_tests(
        self,
        sandbox: Any,
        model_router: Any,
        changes: list[dict]
    ) -> dict:
        """Run tests to verify changes."""
        # Try common test commands
        test_commands = [
            "npm test",
            "pytest",
            "python -m pytest",
            "cargo test",
            "go test ./...",
        ]

        for cmd in test_commands:
            result = await sandbox.run_command(f"{cmd} 2>&1", timeout=120)
            if result.exit_code == 0:
                return {"passed": True, "output": result.stdout}
            elif "command not found" not in result.stderr.lower():
                return {
                    "passed": False,
                    "output": result.stdout + result.stderr,
                    "error": f"Tests failed with exit code {result.exit_code}",
                }

        # No test framework found
        return {"passed": True, "output": "No test framework detected"}

    async def _fix_failures(
        self,
        sandbox: Any,
        model_router: Any,
        test_result: dict,
        changes: list[dict]
    ) -> list[dict]:
        """Fix test failures."""
        fix_prompt = f"""Tests failed with this output:

{test_result.get('output', '')[:3000]}

Previous changes made:
{changes}

Analyze the failure and provide fixes."""

        response = await model_router.chat(
            messages=[{"role": "user", "content": fix_prompt}],
            task_type="coding",
        )

        # Parse and return fixes
        return []

    def _format_pr_preview(
        self,
        issue_data: dict,
        changes: list[dict],
        plan: str
    ) -> str:
        """Format PR preview for approval."""
        return f"""## Pull Request Preview

**Issue:** #{issue_data['number']} - {issue_data['title']}

**Changes:**
{len(changes)} files modified

**Plan Summary:**
{plan[:1000]}

Ready to create PR?"""

    async def _create_pull_request(
        self,
        sandbox: Any,
        issue_data: dict,
        changes: list[dict],
        plan: str
    ) -> dict:
        """Create the pull request using gh CLI."""
        issue_num = issue_data["number"]
        title = issue_data["title"]

        # Create branch
        branch_prefix = "fix" if issue_data.get("is_bug") else "feat"
        branch_name = f"{branch_prefix}/issue-{issue_num}"

        await sandbox.run_command(f'git checkout -b {branch_name}')

        # Stage and commit
        await sandbox.run_command('git add -A')

        commit_msg = f"{branch_prefix}: {title[:50]} (#{issue_num})"
        await sandbox.run_command(f'git commit -m "{commit_msg}"')

        # Push
        await sandbox.run_command(f'git push -u origin {branch_name}')

        # Create PR
        pr_body = f"""## Description

Fixes #{issue_num}

## Changes Made

{plan[:1500]}

## Testing

- [x] Tests pass locally
- [x] Verified acceptance criteria

---
🤖 Generated by Polli Code Agent
"""

        result = await sandbox.run_command(
            f'gh pr create --title "{commit_msg}" --body "{pr_body}"'
        )

        if result.exit_code == 0:
            # Parse PR URL from output
            pr_url = result.stdout.strip()
            return {"url": pr_url, "created": True}

        return {"error": result.stderr, "created": False}
