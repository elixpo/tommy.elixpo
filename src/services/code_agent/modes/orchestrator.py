"""
Orchestrator Mode - The central brain that delegates to specialized modes.

The Orchestrator:
- Knows all available modes and their capabilities
- Analyzes incoming tasks and delegates to appropriate specialized modes
- Receives reports back from modes when they complete
- Can chain multiple modes together for complex tasks
- Acts as "man in the middle" while other modes remain isolated
"""

import logging
from typing import Optional, Any
from dataclasses import dataclass, field

from .base import (
    AgentMode,
    ModeConfig,
    WorkflowStep,
    ToolGroup,
    ProgressCallback,
    ApprovalCallback,
)

logger = logging.getLogger(__name__)


# Mode capability registry - what each mode can do
MODE_CAPABILITIES = {
    "code-reviewer": {
        "description": "Reviews code for quality, bugs, security, and best practices",
        "inputs": ["code", "file_path", "diff", "pr_url", "context"],
        "outputs": ["review_comments", "issues_found", "suggestions", "approval_status"],
        "use_when": [
            "Need to review code quality",
            "Check for bugs or security issues",
            "Validate implementation against requirements",
            "Review PR changes",
            "Internal code quality checks",
        ],
    },
    "bug-fixer": {
        "description": "Fixes bugs from any source - issues, errors, user reports",
        "inputs": ["bug_description", "error_message", "stack_trace", "reproduction_steps", "file_path"],
        "outputs": ["fix_applied", "files_changed", "explanation", "test_results"],
        "use_when": [
            "Fix a specific bug",
            "Resolve an error or exception",
            "Address user-reported issues",
            "Fix failing tests",
        ],
    },
    "feature-builder": {
        "description": "Implements new features from requirements",
        "inputs": ["feature_description", "requirements", "acceptance_criteria", "design_doc"],
        "outputs": ["implementation", "files_created", "files_modified", "tests_added"],
        "use_when": [
            "Implement a new feature",
            "Add new functionality",
            "Build a new component",
            "Extend existing features",
        ],
    },
    "test-writer": {
        "description": "Generates tests for any code",
        "inputs": ["code", "file_path", "function_name", "test_requirements", "coverage_target"],
        "outputs": ["tests_created", "test_file_path", "coverage_report"],
        "use_when": [
            "Generate unit tests",
            "Add integration tests",
            "Improve test coverage",
            "Test new functionality",
        ],
    },
    "refactorer": {
        "description": "Refactors and cleans up code",
        "inputs": ["code", "file_path", "refactor_goals", "constraints"],
        "outputs": ["refactored_code", "changes_made", "improvement_summary"],
        "use_when": [
            "Clean up messy code",
            "Improve code structure",
            "Reduce duplication",
            "Apply design patterns",
            "Performance optimization",
        ],
    },
    "doc-writer": {
        "description": "Generates documentation for code",
        "inputs": ["code", "file_path", "doc_type", "audience"],
        "outputs": ["documentation", "doc_file_path", "api_reference"],
        "use_when": [
            "Generate docstrings",
            "Create README files",
            "Write API documentation",
            "Add inline comments",
        ],
    },
    "researcher": {
        "description": "Searches web and gathers information",
        "inputs": ["query", "topic", "context", "depth"],
        "outputs": ["findings", "sources", "summary", "recommendations"],
        "use_when": [
            "Research a topic",
            "Find best practices",
            "Look up documentation",
            "Investigate errors",
            "Find examples",
        ],
    },
    "investigator": {
        "description": "Investigates issues and proposes solutions without code changes",
        "inputs": ["issue_url", "problem_description", "symptoms"],
        "outputs": ["analysis", "root_cause", "proposed_solutions", "report"],
        "use_when": [
            "Understand a problem before fixing",
            "Analyze complex issues",
            "Create investigation reports",
        ],
    },
    "issue-fixer": {
        "description": "Fixes GitHub issues autonomously end-to-end",
        "inputs": ["issue_url", "repo", "branch"],
        "outputs": ["pr_url", "changes_made", "tests_passed"],
        "use_when": [
            "Fix a GitHub issue completely",
            "End-to-end issue resolution",
        ],
    },
    "pr-fixer": {
        "description": "Fixes PR issues - reviews, tests, conflicts",
        "inputs": ["pr_number", "pr_url", "repo"],
        "outputs": ["fixes_applied", "tests_passed", "conflicts_resolved"],
        "use_when": [
            "Address PR review comments",
            "Fix failing PR checks",
            "Resolve merge conflicts",
        ],
    },
}


@dataclass
class TaskAnalysis:
    """Analysis of a task for delegation."""
    task_type: str
    complexity: str  # "simple", "moderate", "complex"
    required_modes: list[str]
    execution_plan: list[dict]
    parallel_possible: bool = False


class Orchestrator(AgentMode):
    """
    The Orchestrator - central brain that delegates to specialized modes.

    Responsibilities:
    1. Analyze incoming tasks
    2. Determine which mode(s) to use
    3. Delegate tasks to appropriate modes
    4. Collect and synthesize results
    5. Handle multi-mode workflows
    """

    @property
    def config(self) -> ModeConfig:
        return ModeConfig(
            slug="orchestrator",
            name="Orchestrator",
            emoji="🧠",
            role_definition="""You are the Orchestrator - the central intelligence that coordinates all coding tasks.

You have deep knowledge of all available specialized modes and their capabilities:
- CodeReviewer: Reviews code quality, security, best practices
- BugFixer: Fixes bugs from any source
- FeatureBuilder: Implements new features
- TestWriter: Generates tests
- Refactorer: Cleans up and restructures code
- DocWriter: Generates documentation
- Researcher: Web search and information gathering
- Investigator: Analyzes issues without code changes
- IssueFixer: End-to-end GitHub issue resolution
- PRFixer: Fixes PR issues

Your role is to:
1. Understand the task at hand
2. Break it down into subtasks if needed
3. Delegate to the right specialized mode(s)
4. Coordinate between modes when needed
5. Synthesize final results""",
            when_to_use="""Use the Orchestrator when:
- Task requires multiple specialized skills
- Unclear which mode should handle the task
- Complex workflow needs coordination
- Task needs to be broken into subtasks
- Results from multiple modes need synthesis""",
            description="Central coordinator that analyzes tasks and delegates to specialized modes",
            tool_groups=[
                ToolGroup.READ,
                ToolGroup.GITHUB,
                ToolGroup.SEARCH,
            ],
            workflow_steps=[
                WorkflowStep(
                    number=1,
                    name="Analyze Task",
                    instructions="""Analyze the incoming task:
1. Understand what needs to be done
2. Identify the type of task (bug fix, feature, review, etc.)
3. Assess complexity and scope
4. Determine if task needs to be broken down""",
                    tools_required=[ToolGroup.READ],
                ),
                WorkflowStep(
                    number=2,
                    name="Plan Delegation",
                    instructions="""Create a delegation plan:
1. Identify which mode(s) are best suited
2. Determine execution order (sequential vs parallel)
3. Define what context each mode needs
4. Plan how to combine results""",
                    tools_required=[],
                ),
                WorkflowStep(
                    number=3,
                    name="Delegate Tasks",
                    instructions="""Execute the delegation plan:
1. Prepare context for each mode
2. Invoke modes in planned order
3. Pass only necessary information to each mode
4. Collect results from each mode""",
                    tools_required=[ToolGroup.READ, ToolGroup.GITHUB],
                ),
                WorkflowStep(
                    number=4,
                    name="Synthesize Results",
                    instructions="""Combine and present results:
1. Aggregate outputs from all modes
2. Resolve any conflicts or overlaps
3. Create unified summary
4. Present final result to user""",
                    tools_required=[],
                ),
            ],
            best_practices=[
                "Always analyze before delegating - understand the task fully",
                "Use the simplest mode that can handle the task",
                "Chain modes logically - e.g., investigate before fixing",
                "Don't over-delegate - simple tasks don't need orchestration",
                "Provide clear, focused context to each mode",
                "Modes are isolated - don't assume they share state",
                "Validate mode outputs before passing to next mode",
                "Use Researcher mode when external information is needed",
            ],
            custom_instructions="""Mode Selection Guidelines:

For bugs/errors:
1. If unclear → Investigator first to understand
2. If clear → BugFixer directly
3. After fix → TestWriter for regression tests

For new features:
1. If needs research → Researcher first
2. Then → FeatureBuilder for implementation
3. Then → TestWriter for tests
4. Optionally → DocWriter for docs

For code quality:
1. CodeReviewer to identify issues
2. Refactorer or BugFixer based on findings

For GitHub issues:
1. Simple/clear → IssueFixer end-to-end
2. Complex/unclear → Investigator first, then others

For PRs:
1. PRFixer for PR-specific issues
2. CodeReviewer for review comments

Always consider: Can one mode handle this alone, or do we need multiple?""",
        )

    async def execute(
        self,
        context: dict[str, Any],
        sandbox: Any,
        model_router: Any,
        on_progress: Optional[ProgressCallback] = None,
        on_approval: Optional[ApprovalCallback] = None,
    ) -> dict[str, Any]:
        """Execute orchestration workflow."""

        # Step 1: Analyze the task
        await self._report_progress(on_progress, "analyze", "Analyzing task...")

        task = context.get("task", "")
        task_context = context.get("context", {})

        if not task:
            return {"success": False, "error": "No task provided"}

        analysis = await self._analyze_task(
            task=task,
            task_context=task_context,
            model_router=model_router,
        )

        await self._report_progress(
            on_progress,
            "analyze",
            f"Task analysis complete",
            f"Type: {analysis.task_type}, Complexity: {analysis.complexity}, Modes: {analysis.required_modes}"
        )

        # Step 2: Get approval for delegation plan
        if on_approval:
            plan_summary = self._format_execution_plan(analysis)
            decision, feedback = await self._request_approval(
                on_approval,
                "delegation_plan",
                plan_summary,
            )

            if decision == "reject":
                return {"success": False, "error": "Delegation plan rejected", "feedback": feedback}

            if feedback:
                # Re-analyze with feedback
                analysis = await self._analyze_task(
                    task=f"{task}\n\nFeedback: {feedback}",
                    task_context=task_context,
                    model_router=model_router,
                )

        # Step 3: Execute delegation plan
        await self._report_progress(on_progress, "delegate", "Executing delegation plan...")

        results = []
        for step in analysis.execution_plan:
            mode_name = step["mode"]
            mode_context = step["context"]

            await self._report_progress(
                on_progress,
                "delegate",
                f"Delegating to {mode_name}...",
                step.get("description", "")
            )

            # Execute the mode
            result = await self._execute_mode(
                mode_name=mode_name,
                context=mode_context,
                sandbox=sandbox,
                model_router=model_router,
                on_progress=on_progress,
                on_approval=on_approval,
            )

            results.append({
                "mode": mode_name,
                "result": result,
            })

            # Check if we should stop
            if not result.get("success", False) and step.get("required", True):
                await self._report_progress(
                    on_progress,
                    "delegate",
                    f"Mode {mode_name} failed - stopping",
                    result.get("error", "Unknown error")
                )
                break

        # Step 4: Synthesize results
        await self._report_progress(on_progress, "synthesize", "Synthesizing results...")

        final_result = await self._synthesize_results(
            task=task,
            results=results,
            model_router=model_router,
        )

        return {
            "success": all(r["result"].get("success", False) for r in results),
            "analysis": {
                "task_type": analysis.task_type,
                "complexity": analysis.complexity,
                "modes_used": analysis.required_modes,
            },
            "mode_results": results,
            "summary": final_result.get("summary", ""),
            "outputs": final_result.get("outputs", {}),
        }

    async def _analyze_task(
        self,
        task: str,
        task_context: dict,
        model_router: Any,
    ) -> TaskAnalysis:
        """Analyze a task to determine how to handle it."""

        # Build analysis prompt
        modes_info = "\n".join([
            f"- {name}: {info['description']}\n  Use when: {', '.join(info['use_when'][:3])}"
            for name, info in MODE_CAPABILITIES.items()
        ])

        prompt = f"""Analyze this task and determine how to handle it.

TASK: {task}

ADDITIONAL CONTEXT: {task_context}

AVAILABLE MODES:
{modes_info}

Analyze and respond with:
1. TASK_TYPE: What type of task is this? (bug_fix, feature, review, research, investigation, refactor, docs, pr_fix, issue_fix)
2. COMPLEXITY: simple, moderate, or complex
3. REQUIRED_MODES: List of mode slugs needed, in order
4. EXECUTION_PLAN: For each mode, what context/inputs to provide
5. PARALLEL: Can any modes run in parallel? (true/false)

Respond in this exact format:
TASK_TYPE: <type>
COMPLEXITY: <level>
REQUIRED_MODES: <mode1>, <mode2>, ...
PARALLEL: <true/false>
PLAN:
- MODE: <mode_name>
  DESCRIPTION: <what this mode will do>
  CONTEXT: <key info to pass>
  REQUIRED: <true/false - should we stop if this fails?>
"""

        response = await model_router.chat(
            model_id="gemini-large",  # Use Gemini for planning (large context)
            messages=[
                {"role": "system", "content": self.get_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            task_type="planning",
            temperature=0.3,
        )

        content = response.get("content", "")
        return self._parse_analysis(content, task)

    def _parse_analysis(self, content: str, task: str) -> TaskAnalysis:
        """Parse the analysis response."""
        lines = content.strip().split("\n")

        task_type = "unknown"
        complexity = "moderate"
        required_modes = []
        parallel = False
        execution_plan = []

        current_mode = None

        for line in lines:
            line = line.strip()

            if line.startswith("TASK_TYPE:"):
                task_type = line.split(":", 1)[1].strip().lower()
            elif line.startswith("COMPLEXITY:"):
                complexity = line.split(":", 1)[1].strip().lower()
            elif line.startswith("REQUIRED_MODES:"):
                modes_str = line.split(":", 1)[1].strip()
                required_modes = [m.strip() for m in modes_str.split(",")]
            elif line.startswith("PARALLEL:"):
                parallel = "true" in line.lower()
            elif line.startswith("- MODE:"):
                if current_mode:
                    execution_plan.append(current_mode)
                current_mode = {
                    "mode": line.split(":", 1)[1].strip(),
                    "description": "",
                    "context": {},
                    "required": True,
                }
            elif current_mode:
                if line.startswith("DESCRIPTION:"):
                    current_mode["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("CONTEXT:"):
                    current_mode["context"]["info"] = line.split(":", 1)[1].strip()
                elif line.startswith("REQUIRED:"):
                    current_mode["required"] = "true" in line.lower()

        if current_mode:
            execution_plan.append(current_mode)

        # Add task to all mode contexts
        for step in execution_plan:
            step["context"]["task"] = task

        # Fallback if parsing failed
        if not required_modes:
            required_modes = ["investigator"]
            execution_plan = [{
                "mode": "investigator",
                "description": "Investigate the task",
                "context": {"task": task},
                "required": True,
            }]

        return TaskAnalysis(
            task_type=task_type,
            complexity=complexity,
            required_modes=required_modes,
            execution_plan=execution_plan,
            parallel_possible=parallel,
        )

    def _format_execution_plan(self, analysis: TaskAnalysis) -> str:
        """Format the execution plan for approval."""
        lines = [
            f"## Task Analysis",
            f"- **Type**: {analysis.task_type}",
            f"- **Complexity**: {analysis.complexity}",
            f"- **Parallel execution**: {'Yes' if analysis.parallel_possible else 'No'}",
            "",
            "## Execution Plan",
        ]

        for i, step in enumerate(analysis.execution_plan, 1):
            lines.append(f"\n### Step {i}: {step['mode']}")
            lines.append(f"- {step.get('description', 'Execute mode')}")
            if step.get('required'):
                lines.append("- ⚠️ Required - will stop on failure")

        return "\n".join(lines)

    async def _execute_mode(
        self,
        mode_name: str,
        context: dict,
        sandbox: Any,
        model_router: Any,
        on_progress: Optional[ProgressCallback],
        on_approval: Optional[ApprovalCallback],
    ) -> dict:
        """Execute a single mode."""
        try:
            # Import here to avoid circular imports
            from . import get_mode

            mode = get_mode(mode_name)
            result = await mode.execute(
                context=context,
                sandbox=sandbox,
                model_router=model_router,
                on_progress=on_progress,
                on_approval=on_approval,
            )
            return result

        except ValueError as e:
            return {"success": False, "error": f"Unknown mode: {mode_name}"}
        except Exception as e:
            logger.exception(f"Mode {mode_name} failed: {e}")
            return {"success": False, "error": str(e)}

    async def _synthesize_results(
        self,
        task: str,
        results: list[dict],
        model_router: Any,
    ) -> dict:
        """Synthesize results from all modes into a unified output."""

        if not results:
            return {"summary": "No modes executed", "outputs": {}}

        # If only one mode, just return its result
        if len(results) == 1:
            r = results[0]["result"]
            return {
                "summary": r.get("summary", f"Completed {results[0]['mode']}"),
                "outputs": r,
            }

        # Build synthesis prompt
        results_summary = "\n\n".join([
            f"## {r['mode']}\nSuccess: {r['result'].get('success', False)}\nOutput: {r['result']}"
            for r in results
        ])

        prompt = f"""Synthesize the results from multiple modes into a unified summary.

ORIGINAL TASK: {task}

MODE RESULTS:
{results_summary}

Create:
1. A concise summary of what was accomplished
2. Key outputs from each mode
3. Any issues or warnings
4. Next steps if applicable

Respond naturally, not in a rigid format."""

        response = await model_router.chat(
            model_id="claude",  # Use fast Claude for synthesis
            messages=[
                {"role": "user", "content": prompt},
            ],
            task_type="quick",
            temperature=0.3,
        )

        return {
            "summary": response.get("content", "Synthesis complete"),
            "outputs": {r["mode"]: r["result"] for r in results},
        }


# Helper function to get mode capabilities
def get_mode_capabilities(mode_name: str) -> Optional[dict]:
    """Get capabilities for a specific mode."""
    return MODE_CAPABILITIES.get(mode_name)


def list_mode_capabilities() -> dict:
    """List all mode capabilities."""
    return MODE_CAPABILITIES.copy()
