"""
Issue Investigator Mode - Investigate GitHub issues and propose solutions.

This mode is for ANALYSIS ONLY - it doesn't make code changes.
It investigates issues, searches the codebase, and provides detailed
analysis with proposed solutions.

Workflow (inspired by Roo-Code):
1. Retrieve Issue - Get issue details and comments
2. Investigate - Search codebase for related code
3. Analyze - Identify probable causes
4. Propose Solution - Write detailed analysis
5. Report - Post findings to GitHub issue
"""

import re
import json
import logging
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


class Investigator(AgentMode):
    """
    Investigate GitHub issues and propose solutions.

    Given a GitHub issue URL, this mode will:
    1. Fetch issue details
    2. Search codebase for related code
    3. Analyze potential causes
    4. Propose solution(s)
    5. Optionally post analysis to GitHub
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="investigator",
            name="Issue Investigator",
            emoji="🕵️",
            role_definition="""You are a GitHub issue investigator. Your purpose is to analyze GitHub issues, investigate probable causes using extensive codebase searches, and propose well-reasoned, theoretical solutions.

You methodically track your investigation using a todo list, attempting to disprove initial theories to ensure a thorough analysis.

Your final output is a human-like, conversational comment for the GitHub issue that:
- Summarizes the investigation
- Explains probable causes
- Proposes concrete solutions
- Identifies any risks or trade-offs""",
            when_to_use="Use this mode when you need to investigate a GitHub issue to understand its root cause and propose a solution, before implementation begins.",
            description="Investigate GitHub issues and propose solutions.",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.COMMAND,
                ToolGroup.GITHUB,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Retrieve Issue",
                    instructions="""Fetch issue details and all comments:
- gh api repos/{owner}/{repo}/issues/{number}
- gh api repos/{owner}/{repo}/issues/{number}/comments
Document requirements, reproduction steps, and any clarifications.""",
                    tools_required=[ToolGroup.COMMAND, ToolGroup.GITHUB],
                ),
                WorkflowStep(
                    number=2,
                    name="Investigate Codebase",
                    instructions="""Search the codebase thoroughly:
- Search for error messages mentioned in the issue
- Find relevant functions and components
- Trace the code path from user action to error
- Look for similar past issues or related code
- Check git blame for recent changes to relevant files""",
                    tools_required=[ToolGroup.READ, ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=3,
                    name="Analyze and Theorize",
                    instructions="""Form and test hypotheses:
- Identify potential root causes
- Try to DISPROVE each theory
- Consider edge cases
- Look for patterns in similar issues
- Document evidence for/against each theory""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=4,
                    name="Propose Solutions",
                    instructions="""Develop solution proposals:
- For each probable cause, propose a fix
- Consider multiple approaches
- Identify trade-offs and risks
- Estimate complexity/effort
- Note any dependencies or blockers""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=5,
                    name="Write Report",
                    instructions="""Create a detailed analysis report:
- Summary of the issue
- Investigation methodology
- Findings with evidence
- Proposed solutions ranked by preference
- Risks and considerations
- Next steps recommendation""",
                    tools_required=[],
                    requires_approval=True,
                ),
            ],
            best_practices=[
                "Be thorough - check multiple possible causes",
                "Try to disprove theories, not just confirm them",
                "Include code snippets as evidence",
                "Link to specific files and line numbers",
                "Consider the impact of proposed changes",
                "Write in a conversational, helpful tone",
                "Acknowledge uncertainty when appropriate",
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
        """Execute the investigation workflow."""

        # Validate context
        error = self._validate_context(context, ["issue_url"])
        if error:
            return {"success": False, "error": error}

        issue_url = context["issue_url"]
        post_to_github = context.get("post_to_github", False)

        state = ModeState(total_steps=5)

        try:
            # Step 1: Retrieve Issue
            await self._report_progress(on_progress, "retrieving", "Fetching issue details...", None)

            issue_data = await self._get_issue(sandbox, issue_url)
            if issue_data.get("error"):
                return {"success": False, "error": issue_data["error"]}

            state.step_outputs[1] = issue_data

            # Step 2: Investigate Codebase
            state.current_step = 2
            await self._report_progress(on_progress, "investigating", "Searching codebase...", None)

            investigation = await self._investigate(sandbox, model_router, issue_data)
            state.step_outputs[2] = investigation

            # Step 3: Analyze and Theorize
            state.current_step = 3
            await self._report_progress(on_progress, "analyzing", "Analyzing potential causes...", None)

            theories = await self._analyze(model_router, issue_data, investigation)
            state.step_outputs[3] = theories

            # Step 4: Propose Solutions
            state.current_step = 4
            await self._report_progress(on_progress, "proposing", "Developing solutions...", None)

            solutions = await self._propose_solutions(model_router, issue_data, theories)
            state.step_outputs[4] = solutions

            # Step 5: Write Report
            state.current_step = 5
            await self._report_progress(on_progress, "reporting", "Writing analysis report...", None)

            report = await self._write_report(
                model_router, issue_data, investigation, theories, solutions
            )

            # Request approval before posting
            decision, feedback = await self._request_approval(
                on_approval, "report_review", report
            )

            if decision == "modify":
                # Revise report based on feedback
                report = await self._revise_report(model_router, report, feedback)

            # Post to GitHub if approved and requested
            github_comment_url = None
            if decision == "approve" and post_to_github:
                await self._report_progress(on_progress, "posting", "Posting to GitHub...", None)
                github_comment_url = await self._post_to_github(
                    sandbox, issue_data, report
                )

            state.completed = True
            state.success = True

            return {
                "success": True,
                "issue": issue_data,
                "report": report,
                "theories": theories,
                "solutions": solutions,
                "github_comment_url": github_comment_url,
            }

        except Exception as e:
            logger.exception("Investigation workflow failed")
            return {"success": False, "error": str(e)}

    async def _get_issue(self, sandbox: Any, issue_url: str) -> dict:
        """Fetch issue details from GitHub."""
        # Parse URL
        match = re.match(
            r'https://github\.com/([^/]+)/([^/]+)/issues/(\d+)',
            issue_url
        )
        if not match:
            return {"error": f"Invalid issue URL: {issue_url}"}

        owner, repo, number = match.groups()

        # Get issue
        result = await sandbox.run_command(
            f'gh api repos/{owner}/{repo}/issues/{number}'
        )
        if result.exit_code != 0:
            return {"error": f"Failed to fetch issue: {result.stderr}"}

        try:
            issue = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"error": "Failed to parse issue"}

        # Get comments
        comments_result = await sandbox.run_command(
            f'gh api repos/{owner}/{repo}/issues/{number}/comments'
        )
        comments = []
        if comments_result.exit_code == 0:
            try:
                comments = json.loads(comments_result.stdout)
            except:
                pass

        return {
            "owner": owner,
            "repo": repo,
            "number": int(number),
            "title": issue.get("title", ""),
            "body": issue.get("body", ""),
            "author": issue.get("user", {}).get("login", "unknown"),
            "labels": [l.get("name") for l in issue.get("labels", [])],
            "comments": [c.get("body", "") for c in comments],
        }

    async def _investigate(
        self,
        sandbox: Any,
        model_router: Any,
        issue_data: dict
    ) -> dict:
        """Search codebase for relevant information."""
        title = issue_data["title"]
        body = issue_data["body"]

        # Extract search terms using AI
        extract_prompt = f"""Extract key search terms from this issue for codebase investigation:

Title: {title}
Body: {body[:2000]}

Return a JSON object with:
{{
    "error_messages": ["any error messages mentioned"],
    "function_names": ["function or method names"],
    "file_paths": ["any file paths mentioned"],
    "keywords": ["other relevant keywords"]
}}"""

        response = await model_router.chat(
            messages=[{"role": "user", "content": extract_prompt}],
            task_type="quick",
        )

        try:
            search_terms = json.loads(response)
        except:
            search_terms = {"keywords": title.split()[:5]}

        # Perform searches
        findings = {
            "files_found": [],
            "code_snippets": [],
            "git_history": [],
        }

        # Search for each keyword
        all_terms = (
            search_terms.get("error_messages", []) +
            search_terms.get("function_names", []) +
            search_terms.get("keywords", [])
        )

        for term in all_terms[:5]:
            if not term or len(term) < 3:
                continue

            # Search in code
            result = await sandbox.run_command(
                f'grep -rn "{term}" --include="*.py" --include="*.ts" --include="*.js" . 2>/dev/null | head -10'
            )
            if result.exit_code == 0 and result.stdout.strip():
                findings["code_snippets"].append({
                    "term": term,
                    "matches": result.stdout[:1000],
                })

        # Check git history for related changes
        for term in all_terms[:3]:
            if not term:
                continue
            result = await sandbox.run_command(
                f'git log --oneline --all -n 5 --grep="{term}" 2>/dev/null'
            )
            if result.exit_code == 0 and result.stdout.strip():
                findings["git_history"].append({
                    "term": term,
                    "commits": result.stdout,
                })

        return {
            "search_terms": search_terms,
            "findings": findings,
        }

    async def _analyze(
        self,
        model_router: Any,
        issue_data: dict,
        investigation: dict
    ) -> list[dict]:
        """Analyze findings and form theories."""
        analyze_prompt = f"""Analyze this GitHub issue and investigation results. Form theories about the root cause.

## Issue
Title: {issue_data['title']}
Body: {issue_data['body'][:2000]}

## Investigation Findings
{json.dumps(investigation['findings'], indent=2)[:3000]}

For each theory:
1. State the hypothesis
2. List supporting evidence
3. List evidence against
4. Rate confidence (low/medium/high)

Return as JSON array:
[{{"hypothesis": "...", "evidence_for": [...], "evidence_against": [...], "confidence": "..."}}]"""

        response = await model_router.chat(
            messages=[{"role": "user", "content": analyze_prompt}],
            task_type="planning",
        )

        try:
            return json.loads(response)
        except:
            return [{
                "hypothesis": "Unable to parse theories",
                "evidence_for": [],
                "evidence_against": [],
                "confidence": "low",
            }]

    async def _propose_solutions(
        self,
        model_router: Any,
        issue_data: dict,
        theories: list[dict]
    ) -> list[dict]:
        """Propose solutions based on theories."""
        solutions_prompt = f"""Based on these theories about a GitHub issue, propose solutions:

## Issue: {issue_data['title']}

## Theories:
{json.dumps(theories, indent=2)}

For each plausible theory, propose a solution:
1. What files need to change
2. What the fix involves
3. Estimated complexity (low/medium/high)
4. Any risks or trade-offs

Return as JSON array:
[{{"theory": "...", "solution": "...", "files": [...], "complexity": "...", "risks": [...]}}]"""

        response = await model_router.chat(
            messages=[{"role": "user", "content": solutions_prompt}],
            task_type="planning",
        )

        try:
            return json.loads(response)
        except:
            return []

    async def _write_report(
        self,
        model_router: Any,
        issue_data: dict,
        investigation: dict,
        theories: list[dict],
        solutions: list[dict]
    ) -> str:
        """Write the final analysis report."""
        report_prompt = f"""Write a GitHub issue comment with your investigation findings.

## Issue: #{issue_data['number']} - {issue_data['title']}

## Theories (ranked by confidence):
{json.dumps(theories, indent=2)}

## Proposed Solutions:
{json.dumps(solutions, indent=2)}

Write a helpful, conversational comment that:
1. Thanks for reporting (briefly)
2. Summarizes your investigation
3. Explains the most likely cause(s)
4. Proposes solutions with trade-offs
5. Asks any clarifying questions if needed
6. Offers to help implement

Keep it concise but thorough. Use markdown formatting."""

        return await model_router.chat(
            messages=[{"role": "user", "content": report_prompt}],
            task_type="planning",
        )

    async def _revise_report(
        self,
        model_router: Any,
        original_report: str,
        feedback: str
    ) -> str:
        """Revise report based on feedback."""
        revise_prompt = f"""Revise this GitHub issue analysis based on feedback:

## Original Report:
{original_report}

## Feedback:
{feedback}

Revise the report to address the feedback."""

        return await model_router.chat(
            messages=[{"role": "user", "content": revise_prompt}],
            task_type="planning",
        )

    async def _post_to_github(
        self,
        sandbox: Any,
        issue_data: dict,
        report: str
    ) -> Optional[str]:
        """Post the analysis as a comment on the GitHub issue."""
        owner = issue_data["owner"]
        repo = issue_data["repo"]
        number = issue_data["number"]

        # Escape report for shell
        escaped_report = report.replace('"', '\\"').replace('`', '\\`')

        result = await sandbox.run_command(
            f'gh issue comment {number} --repo {owner}/{repo} --body "{escaped_report}"'
        )

        if result.exit_code == 0:
            return f"https://github.com/{owner}/{repo}/issues/{number}#issuecomment-new"
        return None
