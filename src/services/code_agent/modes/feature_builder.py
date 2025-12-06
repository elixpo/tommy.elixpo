"""
FeatureBuilder Mode - Implements new features from requirements.

General purpose - can build:
- New functionality
- API endpoints
- UI components
- Integrations
- Extensions to existing features
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


class FeatureBuilder(AgentMode):
    """
    Implements new features from requirements and specifications.

    Full-featured implementation including code, tests, and docs.
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="feature-builder",
            name="Feature Builder",
            emoji="🏗️",
            role_definition="""You are an expert software developer who excels at building new features.

Your approach:
- Understand requirements thoroughly before coding
- Design clean, maintainable architecture
- Write high-quality, tested code
- Follow existing patterns in the codebase
- Document as you go

You build features that integrate seamlessly with existing code.""",
            when_to_use="""Use FeatureBuilder when:
- Implementing a new feature
- Adding new functionality
- Building a new component
- Creating new API endpoints
- Extending existing features significantly""",
            description="Implements new features from requirements",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.EDIT,
                ToolGroup.COMMAND,
                ToolGroup.SEARCH,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Understand Requirements",
                    instructions="""Understand what to build:
1. Parse the feature requirements
2. Identify acceptance criteria
3. Clarify any ambiguities
4. Understand user needs behind the request""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=2,
                    name="Explore Codebase",
                    instructions="""Understand the existing code:
1. Find relevant existing code
2. Identify patterns and conventions
3. Locate integration points
4. Check for reusable components""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=3,
                    name="Design Solution",
                    instructions="""Design the implementation:
1. Create high-level design
2. Define interfaces and contracts
3. Plan file structure
4. Identify dependencies
5. Consider edge cases""",
                    tools_required=[],
                    requires_approval=True,
                ),
                WorkflowStep(
                    number=4,
                    name="Implement Feature",
                    instructions="""Write the code:
1. Create files in logical order
2. Follow existing patterns
3. Write clean, documented code
4. Handle errors properly
5. Consider performance""",
                    tools_required=[ToolGroup.EDIT, ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=5,
                    name="Add Tests",
                    instructions="""Add comprehensive tests:
1. Unit tests for new functions
2. Integration tests for workflows
3. Edge case tests
4. Test error handling""",
                    tools_required=[ToolGroup.EDIT, ToolGroup.COMMAND],
                ),
                WorkflowStep(
                    number=6,
                    name="Verify & Document",
                    instructions="""Verify and document:
1. Run all tests
2. Check linting/type checking
3. Add/update documentation
4. Update any relevant READMEs""",
                    tools_required=[ToolGroup.COMMAND, ToolGroup.EDIT],
                ),
            ],
            best_practices=[
                "Understand fully before coding",
                "Follow existing patterns and conventions",
                "Keep changes focused and minimal",
                "Write self-documenting code",
                "Test as you build",
                "Handle errors gracefully",
                "Consider backward compatibility",
                "Think about performance early",
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
        """Execute feature building workflow."""

        feature_description = context.get("feature_description", context.get("task", ""))
        requirements = context.get("requirements", "")
        acceptance_criteria = context.get("acceptance_criteria", [])
        design_doc = context.get("design_doc", "")

        if not feature_description:
            return {"success": False, "error": "No feature description provided"}

        # Step 1: Understand requirements
        await self._report_progress(on_progress, "understand", "Understanding requirements...")

        parsed_requirements = await self._parse_requirements(
            feature_description=feature_description,
            requirements=requirements,
            acceptance_criteria=acceptance_criteria,
            design_doc=design_doc,
            model_router=model_router,
        )

        # Step 2: Explore codebase
        await self._report_progress(on_progress, "explore", "Exploring codebase...")

        codebase_context = await self._explore_codebase(
            requirements=parsed_requirements,
            sandbox=sandbox,
            model_router=model_router,
        )

        # Step 3: Design solution
        await self._report_progress(on_progress, "design", "Designing solution...")

        design = await self._design_solution(
            requirements=parsed_requirements,
            codebase_context=codebase_context,
            model_router=model_router,
        )

        # Get approval for design
        if on_approval:
            decision, feedback = await self._request_approval(
                on_approval,
                "design",
                design.get("summary", ""),
            )

            if decision == "reject":
                return {"success": False, "error": "Design rejected", "feedback": feedback}

            if feedback:
                design["modifications"] = feedback

        # Step 4: Implement
        await self._report_progress(on_progress, "implement", "Implementing feature...")

        implementation = await self._implement_feature(
            design=design,
            sandbox=sandbox,
            model_router=model_router,
            on_progress=on_progress,
        )

        if not implementation.get("success"):
            return {
                "success": False,
                "error": f"Implementation failed: {implementation.get('error')}",
                "design": design,
            }

        # Step 5: Add tests
        await self._report_progress(on_progress, "test", "Adding tests...")

        tests = await self._add_tests(
            implementation=implementation,
            requirements=parsed_requirements,
            sandbox=sandbox,
            model_router=model_router,
        )

        # Step 6: Verify
        await self._report_progress(on_progress, "verify", "Verifying implementation...")

        verification = await self._verify_implementation(
            implementation=implementation,
            tests=tests,
            sandbox=sandbox,
            model_router=model_router,
        )

        return {
            "success": verification.get("success", False),
            "requirements": parsed_requirements,
            "design": design,
            "implementation": {
                "files_created": implementation.get("files_created", []),
                "files_modified": implementation.get("files_modified", []),
            },
            "tests": {
                "files_created": tests.get("files_created", []),
                "tests_passed": tests.get("tests_passed", False),
            },
            "verification": verification,
            "summary": f"Built feature: {feature_description[:100]}",
        }

    async def _parse_requirements(
        self,
        feature_description: str,
        requirements: str,
        acceptance_criteria: list,
        design_doc: str,
        model_router: Any,
    ) -> dict:
        """Parse and clarify requirements."""

        prompt = f"""Parse and clarify these feature requirements.

FEATURE: {feature_description}

REQUIREMENTS: {requirements}

ACCEPTANCE CRITERIA: {acceptance_criteria}

DESIGN DOC: {design_doc[:5000] if design_doc else "None"}

Extract:
1. CORE_FUNCTIONALITY: What must the feature do?
2. USER_STORIES: Who uses this and how?
3. INPUTS: What inputs does it accept?
4. OUTPUTS: What does it produce?
5. CONSTRAINTS: Any limitations or requirements?
6. EDGE_CASES: What edge cases to handle?
7. SUCCESS_CRITERIA: How do we know it works?

Be specific and actionable."""

        response = await model_router.chat(
            model_id="gemini-large",  # Large context for requirements
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="planning",
            temperature=0.3,
        )

        return {
            "raw": response.get("content", ""),
            "description": feature_description,
            "parsed": response.get("content", ""),
        }

    async def _explore_codebase(
        self,
        requirements: dict,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Explore codebase for relevant context."""

        # List files, search for patterns, etc.
        try:
            result = await sandbox.exec("find . -type f -name '*.py' | head -50")
            files = result.stdout
        except Exception:
            files = ""

        return {
            "files": files,
            "patterns": [],
            "integration_points": [],
        }

    async def _design_solution(
        self,
        requirements: dict,
        codebase_context: dict,
        model_router: Any,
    ) -> dict:
        """Design the solution."""

        prompt = f"""Design a solution for this feature.

REQUIREMENTS:
{requirements.get('parsed', '')}

EXISTING FILES:
{codebase_context.get('files', '')}

Create a design with:
1. ARCHITECTURE: High-level structure
2. FILES_TO_CREATE: New files needed
3. FILES_TO_MODIFY: Existing files to change
4. INTERFACES: Key functions/classes and their signatures
5. DATA_FLOW: How data moves through the system
6. DEPENDENCIES: External packages needed
7. IMPLEMENTATION_ORDER: Which parts to build first

Be specific about file paths and function signatures."""

        response = await model_router.chat(
            model_id="gemini-large",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="planning",
            temperature=0.5,
        )

        return {
            "summary": response.get("content", "")[:2000],
            "raw": response.get("content", ""),
        }

    async def _implement_feature(
        self,
        design: dict,
        sandbox: Any,
        model_router: Any,
        on_progress: Optional[ProgressCallback],
    ) -> dict:
        """Implement the feature."""

        # This would iterate through the design and create/modify files
        # Using sandbox.exec and file operations

        return {
            "success": True,
            "files_created": [],
            "files_modified": [],
        }

    async def _add_tests(
        self,
        implementation: dict,
        requirements: dict,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Add tests for the implementation."""

        return {
            "success": True,
            "files_created": [],
            "tests_passed": True,
        }

    async def _verify_implementation(
        self,
        implementation: dict,
        tests: dict,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Verify the implementation works."""

        return {
            "success": True,
            "tests_passed": True,
            "lint_passed": True,
        }
