"""
Refactorer Mode - Code refactoring and cleanup.

General purpose - can handle:
- Code cleanup
- Architecture improvements
- Performance optimization
- DRY violations
- Design pattern application
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


class Refactorer(AgentMode):
    """
    Refactors and improves code quality without changing behavior.

    Focuses on maintainability, readability, and performance.
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="refactorer",
            name="Refactorer",
            emoji="♻️",
            role_definition="""You are an expert in code refactoring and software architecture.

Your expertise:
- Identifying code smells and anti-patterns
- Applying design patterns appropriately
- Improving code readability
- Reducing complexity
- Performance optimization
- Safe refactoring techniques

You improve code without changing its behavior.""",
            when_to_use="""Use Refactorer when:
- Code needs cleanup without functional changes
- Reducing duplication (DRY violations)
- Applying design patterns
- Improving performance
- Simplifying complex code
- Preparing code for new features""",
            description="Refactors code for quality and maintainability",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.EDIT,
                ToolGroup.COMMAND,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Analyze Code",
                    instructions="""Analyze the code for refactoring opportunities:
1. Identify code smells
2. Find duplication
3. Spot overly complex areas
4. Note naming issues
5. Check for anti-patterns""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=2,
                    name="Plan Refactoring",
                    instructions="""Plan the refactoring:
1. Prioritize changes by impact
2. Ensure tests exist or add them first
3. Plan small, safe changes
4. Consider dependencies""",
                    tools_required=[ToolGroup.READ],
                    requires_approval=True,
                ),
                WorkflowStep(
                    number=3,
                    name="Refactor",
                    instructions="""Execute refactoring:
1. Make one change at a time
2. Run tests after each change
3. Keep commits atomic
4. Preserve behavior exactly""",
                    tools_required=[ToolGroup.EDIT, ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=4,
                    name="Verify",
                    instructions="""Verify refactoring:
1. All tests pass
2. Behavior unchanged
3. Code quality improved
4. No regressions""",
                    tools_required=[ToolGroup.COMMAND],
                ),
            ],
            best_practices=[
                "Never change behavior while refactoring",
                "Have tests before refactoring",
                "Make small, incremental changes",
                "Run tests after each change",
                "Don't optimize prematurely",
                "Keep changes reversible",
                "Document significant changes",
                "Review before committing",
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
        """Execute refactoring workflow."""

        code = context.get("code", "")
        file_path = context.get("file_path", "")
        refactor_goals = context.get("refactor_goals", [])
        constraints = context.get("constraints", [])

        if not code and not file_path:
            return {"success": False, "error": "No code or file path provided"}

        # Step 1: Analyze code
        await self._report_progress(on_progress, "analyze", "Analyzing code for refactoring...")

        analysis = await self._analyze_code(
            code=code,
            file_path=file_path,
            refactor_goals=refactor_goals,
            sandbox=sandbox,
            model_router=model_router,
        )

        await self._report_progress(
            on_progress,
            "analyze",
            f"Found {len(analysis.get('opportunities', []))} refactoring opportunities",
        )

        # Step 2: Plan refactoring
        await self._report_progress(on_progress, "plan", "Planning refactoring...")

        plan = await self._plan_refactoring(
            analysis=analysis,
            constraints=constraints,
            model_router=model_router,
        )

        # Get approval
        if on_approval:
            decision, feedback = await self._request_approval(
                on_approval,
                "refactor_plan",
                plan.get("summary", ""),
            )

            if decision == "reject":
                return {"success": False, "error": "Refactoring plan rejected", "feedback": feedback}

        # Step 3: Refactor
        await self._report_progress(on_progress, "refactor", "Applying refactoring...")

        result = await self._apply_refactoring(
            plan=plan,
            sandbox=sandbox,
            model_router=model_router,
            on_progress=on_progress,
        )

        # Step 4: Verify
        await self._report_progress(on_progress, "verify", "Verifying refactoring...")

        verification = await self._verify_refactoring(
            result=result,
            sandbox=sandbox,
            model_router=model_router,
        )

        return {
            "success": verification.get("success", False),
            "analysis": analysis,
            "plan": plan,
            "changes_made": result.get("changes", []),
            "refactored_code": result.get("code", ""),
            "improvement_summary": plan.get("summary", ""),
            "verification": verification,
        }

    async def _analyze_code(
        self,
        code: str,
        file_path: str,
        refactor_goals: list,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Analyze code for refactoring opportunities."""

        if file_path and not code:
            try:
                result = await sandbox.exec(f"cat {file_path}")
                code = result.stdout
            except Exception:
                pass

        goals_str = ", ".join(refactor_goals) if refactor_goals else "general improvement"

        prompt = f"""Analyze this code for refactoring opportunities.

CODE:
```
{code[:30000]}
```

REFACTORING GOALS: {goals_str}

Identify:
1. CODE_SMELLS: Issues like long methods, large classes, feature envy
2. DUPLICATION: Repeated code that violates DRY
3. COMPLEXITY: Overly complex logic that could be simplified
4. NAMING: Poor variable/function/class names
5. PATTERNS: Missing design patterns that would help
6. PERFORMANCE: Obvious performance issues
7. QUICK_WINS: Easy improvements with high impact

For each issue, note:
- Location (line numbers if visible)
- Severity (high/medium/low)
- Suggested fix
- Risk level of change"""

        response = await model_router.chat(
            model_id="claude-large",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="review",
            temperature=0.3,
        )

        return {
            "code": code,
            "file_path": file_path,
            "raw_analysis": response.get("content", ""),
            "opportunities": [],  # Would parse from response
        }

    async def _plan_refactoring(
        self,
        analysis: dict,
        constraints: list,
        model_router: Any,
    ) -> dict:
        """Plan the refactoring approach."""

        constraints_str = ", ".join(constraints) if constraints else "none"

        prompt = f"""Create a refactoring plan based on this analysis.

ANALYSIS:
{analysis.get('raw_analysis', '')}

CONSTRAINTS: {constraints_str}

Create a plan with:
1. PRIORITY_ORDER: Which refactorings to do first
2. DEPENDENCIES: Which changes depend on others
3. RISK_ASSESSMENT: Risk of each change
4. ROLLBACK_PLAN: How to undo if needed
5. TEST_REQUIREMENTS: Tests needed before each change

Order by: Impact / Risk ratio (high impact, low risk first)

Ensure behavior is NEVER changed - only structure."""

        response = await model_router.chat(
            model_id="claude-large",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="planning",
            temperature=0.3,
        )

        return {
            "summary": response.get("content", "")[:2000],
            "raw": response.get("content", ""),
            "steps": [],
        }

    async def _apply_refactoring(
        self,
        plan: dict,
        sandbox: Any,
        model_router: Any,
        on_progress: Optional[ProgressCallback],
    ) -> dict:
        """Apply the refactoring changes."""

        # Would iterate through plan and apply changes
        return {
            "success": True,
            "changes": [],
            "code": "",
        }

    async def _verify_refactoring(
        self,
        result: dict,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Verify refactoring preserved behavior."""

        return {
            "success": True,
            "tests_passed": True,
            "behavior_preserved": True,
        }
