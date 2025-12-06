"""
DocWriter Mode - Documentation generation.

General purpose - can create:
- Docstrings
- README files
- API documentation
- Code comments
- User guides
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


class DocWriter(AgentMode):
    """
    Generates documentation for code and projects.

    Creates clear, useful documentation that helps developers.
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="doc-writer",
            name="Doc Writer",
            emoji="📝",
            role_definition="""You are an expert technical writer specializing in software documentation.

Your expertise:
- Writing clear, concise documentation
- API reference documentation
- Code comments and docstrings
- README and guide creation
- Documentation for different audiences

You write documentation that developers actually want to read.""",
            when_to_use="""Use DocWriter when:
- Adding docstrings to functions/classes
- Creating README files
- Writing API documentation
- Adding inline comments
- Creating user guides
- Documenting architecture""",
            description="Generates documentation for code and projects",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.EDIT,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Understand Code",
                    instructions="""Understand the code to document:
1. Read the code thoroughly
2. Understand its purpose
3. Identify the audience
4. Note key concepts""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=2,
                    name="Plan Documentation",
                    instructions="""Plan what to document:
1. Identify what needs docs
2. Choose appropriate format
3. Determine level of detail
4. Plan structure""",
                    tools_required=[],
                ),
                WorkflowStep(
                    number=3,
                    name="Write Documentation",
                    instructions="""Write the documentation:
1. Use clear, simple language
2. Include examples
3. Document parameters and returns
4. Add warnings for gotchas""",
                    tools_required=[ToolGroup.EDIT],
                ),
                WorkflowStep(
                    number=4,
                    name="Review",
                    instructions="""Review documentation:
1. Check accuracy
2. Verify completeness
3. Test examples
4. Proofread""",
                    tools_required=[ToolGroup.READ],
                ),
            ],
            best_practices=[
                "Write for your audience",
                "Be concise but complete",
                "Include examples",
                "Document the 'why', not just 'what'",
                "Keep docs close to code",
                "Update docs with code changes",
                "Use consistent style",
                "Test all code examples",
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
        """Execute documentation workflow."""

        code = context.get("code", "")
        file_path = context.get("file_path", "")
        doc_type = context.get("doc_type", "docstring")  # docstring, readme, api, inline
        audience = context.get("audience", "developers")

        if not code and not file_path:
            return {"success": False, "error": "No code or file path provided"}

        # Step 1: Understand code
        await self._report_progress(on_progress, "understand", "Understanding code...")

        code_understanding = await self._understand_code(
            code=code,
            file_path=file_path,
            sandbox=sandbox,
            model_router=model_router,
        )

        # Step 2: Plan documentation
        await self._report_progress(on_progress, "plan", "Planning documentation...")

        doc_plan = await self._plan_documentation(
            code_understanding=code_understanding,
            doc_type=doc_type,
            audience=audience,
            model_router=model_router,
        )

        # Step 3: Write documentation
        await self._report_progress(on_progress, "write", "Writing documentation...")

        documentation = await self._write_documentation(
            code_understanding=code_understanding,
            doc_plan=doc_plan,
            doc_type=doc_type,
            audience=audience,
            model_router=model_router,
        )

        return {
            "success": True,
            "documentation": documentation.get("content", ""),
            "doc_file_path": documentation.get("file_path", ""),
            "doc_type": doc_type,
            "summary": f"Created {doc_type} documentation",
        }

    async def _understand_code(
        self,
        code: str,
        file_path: str,
        sandbox: Any,
        model_router: Any,
    ) -> dict:
        """Understand the code to document."""

        if file_path and not code:
            try:
                result = await sandbox.exec(f"cat {file_path}")
                code = result.stdout
            except Exception:
                pass

        prompt = f"""Analyze this code to understand what needs to be documented.

CODE:
```
{code[:30000]}
```

FILE PATH: {file_path}

Identify:
1. PURPOSE: What does this code do?
2. COMPONENTS: Main classes, functions, modules
3. INTERFACES: Public APIs and their signatures
4. DEPENDENCIES: What this code uses
5. USAGE: How should this code be used?
6. GOTCHAS: Any non-obvious behaviors or limitations
7. EXAMPLES: Good example use cases

Be thorough - good docs come from good understanding."""

        response = await model_router.chat(
            model_id="gemini-large",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="understanding",
            temperature=0.3,
        )

        return {
            "code": code,
            "file_path": file_path,
            "understanding": response.get("content", ""),
        }

    async def _plan_documentation(
        self,
        code_understanding: dict,
        doc_type: str,
        audience: str,
        model_router: Any,
    ) -> dict:
        """Plan what documentation to create."""

        return {
            "doc_type": doc_type,
            "audience": audience,
            "sections": [],
        }

    async def _write_documentation(
        self,
        code_understanding: dict,
        doc_plan: dict,
        doc_type: str,
        audience: str,
        model_router: Any,
    ) -> dict:
        """Write the actual documentation."""

        doc_type_instructions = {
            "docstring": "Write Google-style docstrings for all functions and classes",
            "readme": "Write a comprehensive README.md with overview, installation, usage, and examples",
            "api": "Write detailed API reference documentation",
            "inline": "Add inline comments explaining complex logic",
        }

        prompt = f"""Write {doc_type} documentation for this code.

CODE UNDERSTANDING:
{code_understanding.get('understanding', '')}

ORIGINAL CODE:
```
{code_understanding.get('code', '')[:20000]}
```

AUDIENCE: {audience}
DOC TYPE: {doc_type}
INSTRUCTIONS: {doc_type_instructions.get(doc_type, 'Write appropriate documentation')}

Write documentation that is:
1. Clear and concise
2. Accurate to the code
3. Includes examples
4. Appropriate for the audience
5. Following best practices for {doc_type}

Provide the complete documentation."""

        response = await model_router.chat(
            model_id="claude-large",
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="coding",
            temperature=0.3,
        )

        return {
            "content": response.get("content", ""),
            "file_path": "",
        }
