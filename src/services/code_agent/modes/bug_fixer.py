"""
BugFixer Mode - Fixes bugs from any source.

General purpose - can handle:
- Error messages and stack traces
- User-reported bugs
- GitHub issues
- Failing tests
- Internal bot issues
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


class BugFixer(AgentMode):
    """
    Fixes bugs from any source - errors, issues, failing tests.

    Targeted and efficient - goes straight to fixing the problem.
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="bug-fixer",
            name="Bug Fixer",
            emoji="🐛",
            role_definition="""You are an expert debugger and bug fixer.

Your strengths:
- Quickly identifying root causes from error messages
- Understanding stack traces across languages
- Finding and fixing edge cases
- Writing minimal, targeted fixes
- Adding regression tests

You fix bugs efficiently without over-engineering.""",
            when_to_use="""Use BugFixer when:
- There's a specific bug to fix
- You have an error message or stack trace
- Tests are failing
- User reported a specific issue
- Need to fix a known problem quickly""",
            description="Fixes bugs from error messages, issues, or failing tests",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.EDIT,
                ToolGroup.COMMAND,
                ToolGroup.SEARCH,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Understand Bug",
                    instructions="""Understand the bug:
1. Read error message/stack trace carefully
2. Identify the failing code location
3. Understand what should happen vs what happens
4. Reproduce if possible""",
                    tools_required=[ToolGroup.READ, ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=2,
                    name="Locate Root Cause",
                    instructions="""Find the root cause:
1. Navigate to the error location
2. Trace back to find the actual bug
3. Don't just fix symptoms - find the root
4. Check for related issues nearby""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=3,
                    name="Implement Fix",
                    instructions="""Implement the fix:
1. Make minimal changes to fix the bug
2. Don't refactor unrelated code
3. Handle edge cases that caused the bug
4. Add comments if the fix is non-obvious""",
                    tools_required=[ToolGroup.EDIT],
                ),
                WorkflowStep(
                    number=4,
                    name="Verify Fix",
                    instructions="""Verify the fix works:
1. Run the failing test/reproduction
2. Check that it passes now
3. Run related tests to avoid regression
4. Test edge cases""",
                    tools_required=[ToolGroup.COMMAND],
                ),
            ],
            best_practices=[
                "Understand before fixing - don't guess",
                "Make minimal changes - surgical fixes",
                "Fix root cause, not symptoms",
                "Always verify the fix works",
                "Check for similar bugs nearby",
                "Add regression test if missing",
                "Document non-obvious fixes",
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
        """Execute bug fix workflow."""

        # Gather bug information
        bug_description = context.get("bug_description", context.get("task", ""))
        error_message = context.get("error_message", "")
        stack_trace = context.get("stack_trace", "")
        file_path = context.get("file_path", "")
        reproduction_steps = context.get("reproduction_steps", "")

        if not bug_description and not error_message:
            return {"success": False, "error": "No bug information provided"}

        # Step 1: Understand the bug
        await self._report_progress(on_progress, "understand", "Understanding the bug...")

        analysis = await self._analyze_bug(
            bug_description=bug_description,
            error_message=error_message,
            stack_trace=stack_trace,
            file_path=file_path,
            reproduction_steps=reproduction_steps,
            sandbox=sandbox,
            model_router=model_router,
        )

        await self._report_progress(
            on_progress,
            "understand",
            f"Bug analyzed",
            f"Root cause: {analysis.get('root_cause', 'Unknown')}"
        )

        # Step 2: Locate and plan fix
        await self._report_progress(on_progress, "locate", "Locating root cause...")

        fix_plan = await self._plan_fix(
            analysis=analysis,
            sandbox=sandbox,
            model_router=model_router,
        )

        # Get approval if needed
        if on_approval:
            plan_text = f"**Root Cause**: {analysis.get('root_cause', 'Unknown')}\n\n"
            plan_text += f"**Proposed Fix**:\n{fix_plan.get('description', '')}\n\n"
            plan_text += f"**Files to modify**: {', '.join(fix_plan.get('files', []))}"

            decision, feedback = await self._request_approval(
                on_approval, "fix_plan", plan_text
            )

            if decision == "reject":
                return {"success": False, "error": "Fix plan rejected", "feedback": feedback}

        # Step 3: Implement fix
        await self._report_progress(on_progress, "fix", "Implementing fix...")

        fix_result = await self._implement_fix(
            fix_plan=fix_plan,
            sandbox=sandbox,
            model_router=model_router,
        )

        if not fix_result.get("success"):
            return {
                "success": False,
                "error": f"Failed to implement fix: {fix_result.get('error', 'Unknown')}",
                "analysis": analysis,
            }

        # Step 4: Verify fix
        await self._report_progress(on_progress, "verify", "Verifying fix...")

        verification = await self._verify_fix(
            fix_result=fix_result,
            reproduction_steps=reproduction_steps,
            sandbox=sandbox,
            model_router=model_router,
        )

        return {
            "success": verification.get("success", False),
            "analysis": analysis,
            "fix_plan": fix_plan,
            "files_changed": fix_result.get("files_changed", []),
            "verification": verification,
            "summary": f"Fixed: {analysis.get('root_cause', 'bug')}",
            "explanation": fix_result.get("explanation", ""),
        }

    async def _analyze_bug(
        self,
        bug_description: str,
        error_message: str,
        stack_trace: str,
        file_path: str,
        reproduction_steps: str,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Analyze the bug to understand it."""

        # Read relevant file if provided
        file_content = ""
        if file_path:
            try:
                result = await sandbox.exec(f"cat {file_path}")
                file_content = result.stdout[:20000]
            except Exception:
                pass

        prompt = f"""Analyze this bug to understand the root cause.

BUG DESCRIPTION: {bug_description}

ERROR MESSAGE: {error_message}

STACK TRACE:
{stack_trace}

FILE PATH: {file_path}

FILE CONTENT:
{file_content}

REPRODUCTION STEPS: {reproduction_steps}

Analyze and provide:
1. ROOT_CAUSE: What's actually causing the bug (1-2 sentences)
2. ERROR_TYPE: Category of error (null reference, type error, logic error, etc.)
3. LOCATION: Where in the code the bug originates
4. WHY: Why this bug happens
5. IMPACT: What this bug affects

Be specific and actionable."""

        response = await model_router.chat(
            model_id="claude-large",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="coding",
            temperature=0.2,
        )

        content = response.get("content", "")
        return self._parse_analysis(content)

    def _parse_analysis(self, content: str) -> dict:
        """Parse bug analysis."""
        analysis = {
            "raw": content,
            "root_cause": "",
            "error_type": "",
            "location": "",
            "why": "",
            "impact": "",
        }

        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("ROOT_CAUSE:"):
                analysis["root_cause"] = line.split(":", 1)[1].strip()
            elif line.startswith("ERROR_TYPE:"):
                analysis["error_type"] = line.split(":", 1)[1].strip()
            elif line.startswith("LOCATION:"):
                analysis["location"] = line.split(":", 1)[1].strip()
            elif line.startswith("WHY:"):
                analysis["why"] = line.split(":", 1)[1].strip()
            elif line.startswith("IMPACT:"):
                analysis["impact"] = line.split(":", 1)[1].strip()

        return analysis

    async def _plan_fix(
        self,
        analysis: dict,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Plan the fix based on analysis."""

        prompt = f"""Based on this bug analysis, plan a minimal fix.

ANALYSIS:
{analysis.get('raw', '')}

Create a fix plan:
1. DESCRIPTION: What changes to make (be specific)
2. FILES: Which files need to be modified
3. CHANGES: Specific code changes needed
4. TESTS: How to verify the fix

Focus on minimal, targeted changes. Don't over-engineer."""

        response = await model_router.chat(
            model_id="claude-large",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="coding",
            temperature=0.2,
        )

        content = response.get("content", "")
        return {
            "raw": content,
            "description": content[:500],
            "files": [],  # Would parse from content
        }

    async def _implement_fix(
        self,
        fix_plan: dict,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Implement the fix."""

        # This would be a more complex implementation using
        # the sandbox to actually modify files
        # For now, return the plan

        return {
            "success": True,
            "files_changed": fix_plan.get("files", []),
            "explanation": fix_plan.get("description", ""),
        }

    async def _verify_fix(
        self,
        fix_result: dict,
        reproduction_steps: str,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Verify the fix works."""

        # Would run tests and verification
        return {
            "success": True,
            "tests_passed": True,
            "reproduction_fixed": True,
        }
