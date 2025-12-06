"""
CodeReviewer Mode - Reviews any code for quality, bugs, security, and best practices.

General purpose - can be used:
- By Orchestrator for delegated reviews
- Internally by the bot for quality checks
- Standalone for PR reviews or file reviews
"""

import logging
from typing import Optional, Any

from .base import (
    AgentMode,
    ModeConfig,
    WorkflowStep,
    ToolGroup,
    ProgressCallback,
    ApprovalCallback,
)

logger = logging.getLogger(__name__)


class CodeReviewer(AgentMode):
    """
    Reviews code for quality, bugs, security issues, and best practices.

    General purpose reviewer that can analyze:
    - Individual files or functions
    - Git diffs
    - PR changes
    - Entire modules
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="code-reviewer",
            name="Code Reviewer",
            emoji="🔍",
            role_definition="""You are an expert code reviewer with deep knowledge of software engineering best practices.

Your expertise includes:
- Code quality and readability
- Bug detection and edge cases
- Security vulnerabilities (OWASP Top 10)
- Performance optimization
- Design patterns and architecture
- Language-specific idioms and conventions

You provide constructive, actionable feedback that helps improve code quality.""",
            when_to_use="""Use CodeReviewer when:
- Reviewing code for quality issues
- Checking for potential bugs
- Auditing for security vulnerabilities
- Validating implementation against requirements
- PR code review
- Pre-commit quality checks""",
            description="Reviews code for quality, bugs, security, and best practices",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.SEARCH,  # For looking up best practices
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Gather Code",
                    instructions="""Gather the code to review:
- If file_path provided: Read the file
- If diff provided: Parse the diff
- If pr_url provided: Fetch PR changes
- If code provided directly: Use as-is

Understand the context and purpose of the code.""",
                    tools_required=[ToolGroup.READ, ToolGroup.GITHUB],
                ),
                WorkflowStep(
                    number=2,
                    name="Analyze Quality",
                    instructions="""Analyze code quality:
1. Readability - Is the code clear and well-structured?
2. Naming - Are variables, functions, classes named well?
3. Comments - Are complex parts documented?
4. DRY - Is there unnecessary duplication?
5. SOLID - Does it follow good design principles?
6. Complexity - Is the code unnecessarily complex?""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=3,
                    name="Check for Bugs",
                    instructions="""Look for potential bugs:
1. Edge cases - Empty inputs, null values, boundaries
2. Error handling - Are errors caught and handled?
3. Race conditions - Any concurrency issues?
4. Resource leaks - Are resources properly closed?
5. Logic errors - Incorrect conditions, off-by-one errors
6. Type issues - Type mismatches, implicit conversions""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=4,
                    name="Security Audit",
                    instructions="""Check for security issues:
1. Injection - SQL, command, XSS vulnerabilities
2. Authentication - Proper auth checks
3. Authorization - Access control issues
4. Data exposure - Sensitive data leaks
5. Input validation - Untrusted input handling
6. Dependencies - Known vulnerable packages""",
                    tools_required=[ToolGroup.READ, ToolGroup.SEARCH],
                ),
                WorkflowStep(
                    number=5,
                    name="Generate Report",
                    instructions="""Generate the review report:
1. Summary - Overall assessment
2. Critical issues - Must fix
3. Suggestions - Nice to have
4. Positive notes - What's done well
5. Approval status - approve/request_changes/comment""",
                    tools_required=[],
                ),
            ],
            best_practices=[
                "Be constructive - suggest solutions, not just problems",
                "Prioritize issues - critical bugs first, style last",
                "Be specific - point to exact lines and explain why",
                "Consider context - understand the code's purpose",
                "Don't nitpick - focus on meaningful issues",
                "Acknowledge good code - positive feedback matters",
                "Check tests - is the code properly tested?",
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
        """Execute code review workflow."""

        # Step 1: Gather code to review
        await self._report_progress(on_progress, "gather", "Gathering code to review...")

        code = await self._gather_code(context, sandbox, model_router)
        if not code:
            return {"success": False, "error": "No code provided for review"}

        code_context = context.get("context", "")
        requirements = context.get("requirements", "")
        focus_areas = context.get("focus_areas", [])

        # Step 2-4: Analyze with AI
        await self._report_progress(on_progress, "analyze", "Analyzing code...")

        review = await self._analyze_code(
            code=code,
            code_context=code_context,
            requirements=requirements,
            focus_areas=focus_areas,
            model_router=model_router,
        )

        # Step 5: Generate report
        await self._report_progress(on_progress, "report", "Generating review report...")

        report = self._format_report(review)

        return {
            "success": True,
            "review": review,
            "report": report,
            "issues_found": review.get("issues", []),
            "suggestions": review.get("suggestions", []),
            "approval_status": review.get("approval_status", "comment"),
            "summary": review.get("summary", "Review complete"),
        }

    async def _gather_code(
        self,
        context: dict,
        sandbox: Any,
        model_router: Any,
    ) -> str:
        """Gather code from various sources."""

        # Direct code
        if "code" in context:
            return context["code"]

        # File path
        if "file_path" in context:
            try:
                result = await sandbox.exec(f"cat {context['file_path']}")
                return result.stdout
            except Exception as e:
                logger.warning(f"Failed to read file: {e}")

        # Diff
        if "diff" in context:
            return context["diff"]

        # PR URL - extract diff
        if "pr_url" in context:
            try:
                # Parse PR URL
                pr_url = context["pr_url"]
                # Extract owner/repo/pr_number from URL
                parts = pr_url.rstrip("/").split("/")
                pr_number = parts[-1]
                repo = f"{parts[-4]}/{parts[-3]}"

                result = await sandbox.exec(f"gh pr diff {pr_number} --repo {repo}")
                return result.stdout
            except Exception as e:
                logger.warning(f"Failed to get PR diff: {e}")

        return ""

    async def _analyze_code(
        self,
        code: str,
        code_context: str,
        requirements: str,
        focus_areas: list[str],
        model_router: Any,
    ) -> dict:
        """Analyze code with AI."""

        focus_str = ", ".join(focus_areas) if focus_areas else "quality, bugs, security"

        prompt = f"""Review this code thoroughly.

CODE:
```
{code[:50000]}  # Limit code length
```

CONTEXT: {code_context or "General code review"}

REQUIREMENTS: {requirements or "Follow best practices"}

FOCUS AREAS: {focus_str}

Provide a comprehensive review covering:

1. **Summary**: Overall assessment (1-2 sentences)

2. **Quality Score**: Rate 1-10 with brief justification

3. **Critical Issues** (must fix):
   - Issue description
   - Location (line numbers if visible)
   - Why it's critical
   - Suggested fix

4. **Bugs/Potential Bugs**:
   - Description
   - Location
   - How it could fail
   - Fix suggestion

5. **Security Issues**:
   - Vulnerability type
   - Location
   - Risk level (high/medium/low)
   - Remediation

6. **Suggestions** (nice to have):
   - What to improve
   - Why it would help
   - How to improve it

7. **Positive Notes**: What's done well

8. **Approval Status**: One of:
   - approve: Code is good to go
   - request_changes: Critical issues must be fixed
   - comment: Suggestions but can proceed

Format as structured markdown."""

        response = await model_router.chat(
            model_id="claude-large",  # Best code understanding
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="review",
            temperature=0.3,
        )

        content = response.get("content", "")
        return self._parse_review(content)

    def _parse_review(self, content: str) -> dict:
        """Parse the AI review response."""
        review = {
            "raw": content,
            "summary": "",
            "quality_score": 0,
            "issues": [],
            "bugs": [],
            "security": [],
            "suggestions": [],
            "positive": [],
            "approval_status": "comment",
        }

        # Extract approval status
        content_lower = content.lower()
        if "approve" in content_lower and "request_changes" not in content_lower:
            review["approval_status"] = "approve"
        elif "request_changes" in content_lower:
            review["approval_status"] = "request_changes"

        # Try to extract quality score
        import re
        score_match = re.search(r"quality.*?(\d+)\s*/?\s*10", content_lower)
        if score_match:
            review["quality_score"] = int(score_match.group(1))

        # Extract summary - first paragraph after "Summary"
        summary_match = re.search(r"summary[:\s*]+(.+?)(?:\n\n|\n#)", content, re.IGNORECASE | re.DOTALL)
        if summary_match:
            review["summary"] = summary_match.group(1).strip()[:500]

        return review

    def _format_report(self, review: dict) -> str:
        """Format the review as a readable report."""
        lines = [
            "# Code Review Report",
            "",
            f"**Summary**: {review.get('summary', 'Review complete')}",
            f"**Quality Score**: {review.get('quality_score', 'N/A')}/10",
            f"**Status**: {review.get('approval_status', 'comment').upper()}",
            "",
            "---",
            "",
            review.get("raw", ""),
        ]
        return "\n".join(lines)
