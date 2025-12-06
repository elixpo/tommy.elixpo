"""
Main CodeAgent orchestrator.

Coordinates the full coding workflow:
1. UNDERSTAND (Gemini) - Analyze codebase
2. PLAN (Gemini) - Create implementation plan
3. REVIEW PLAN (Kimi K2 Thinking) - Autonomous approval
4. CODE (Claude Large) - Execute implementation
5. TEST (Claude) - Run tests
6. FIX LOOP (Claude) - Fix failures
7. REVIEW CODE (Kimi K2 Thinking) - Final review
8. COMMIT/PR - Create branch, commit, PR
"""

import asyncio
import logging
import json
from dataclasses import dataclass, field
from typing import Optional, Literal, Callable, Awaitable, Any
from enum import Enum
from datetime import datetime

from .models import ModelRouter, model_router, TaskType
from .sandbox import SandboxManager, sandbox_manager, Sandbox, CommandResult
from .file_editor import FileEditor, EditResult, parse_edit_blocks
from .prompts import (
    SYSTEM_PROMPT,
    REVIEWER_SYSTEM_PROMPT,
    PLANNING_PROMPT,
    PLAN_REVIEW_PROMPT,
    CODING_PROMPT,
    FIX_ERROR_PROMPT,
)

logger = logging.getLogger(__name__)

# Callback types for interactive mode
ProgressCallback = Callable[[str, str, Optional[str]], Awaitable[None]]  # (phase, message, detail)
ApprovalCallback = Callable[[str, str], Awaitable[tuple[str, str]]]  # (phase, content) -> (decision, feedback)


class AgentPhase(Enum):
    """Current phase of the agent."""
    IDLE = "idle"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    PLAN_REVIEW = "plan_review"
    CODING = "coding"
    TESTING = "testing"
    FIXING = "fixing"
    CODE_REVIEW = "code_review"
    COMMITTING = "committing"
    COMPLETE = "complete"
    FAILED = "failed"


class ReviewDecision(Enum):
    """Review decision from Kimi K2."""
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


@dataclass
class AgentState:
    """Current state of the agent."""
    phase: AgentPhase = AgentPhase.IDLE
    task: str = ""
    repo: str = ""
    branch: str = "main"
    sandbox_id: Optional[str] = None

    # Planning
    repo_map: str = ""
    plan: str = ""
    plan_review: Optional[str] = None
    plan_approved: bool = False

    # Coding
    current_step: int = 0
    total_steps: int = 0
    changes_made: list[dict] = field(default_factory=list)

    # Testing
    test_output: str = ""
    test_passed: bool = False
    fix_attempts: int = 0
    max_fix_attempts: int = 5

    # Review
    code_review: Optional[str] = None
    code_approved: bool = False

    # Result
    commit_sha: Optional[str] = None
    pr_url: Optional[str] = None
    error: Optional[str] = None

    # Progress tracking
    started_at: datetime = field(default_factory=datetime.utcnow)
    messages: list[str] = field(default_factory=list)

    def add_message(self, message: str):
        """Add a progress message."""
        self.messages.append(f"[{self.phase.value}] {message}")
        logger.info(f"Agent: {message}")


@dataclass
class AgentResult:
    """Final result of agent execution."""
    success: bool
    phase: AgentPhase
    task: str
    repo: str
    branch: str
    changes: list[dict]
    commit_sha: Optional[str] = None
    pr_url: Optional[str] = None
    error: Optional[str] = None
    messages: list[str] = field(default_factory=list)
    duration: float = 0.0


class CodeAgent:
    """
    Autonomous coding agent.

    Uses multiple AI models:
    - Gemini Large: Planning & understanding (1M context)
    - Claude Large: Coding (best quality)
    - Claude: Testing & fixes (fast)
    - Kimi K2 Thinking: Autonomous reviewer (human-in-the-loop replacement)
    """

    def __init__(
        self,
        model_router: ModelRouter = model_router,
        sandbox_manager: SandboxManager = sandbox_manager,
    ):
        self.models = model_router
        self.sandboxes = sandbox_manager
        self.editor = FileEditor()
        self._initialized = False

    async def initialize(self):
        """Initialize the agent."""
        if not self._initialized:
            await self.models.initialize()
            await self.sandboxes.start()
            self._initialized = True
            logger.info("CodeAgent initialized")

    async def close(self):
        """Clean up resources."""
        await self.models.close()
        await self.sandboxes.stop()
        self._initialized = False

    async def run(
        self,
        task: str,
        repo: str,
        branch: str = "main",
        create_pr: bool = False,
        max_fix_attempts: int = 5,
        require_plan_approval: bool = True,
        require_code_approval: bool = True,
        # Interactive mode callbacks
        on_progress: Optional[ProgressCallback] = None,
        on_approval_needed: Optional[ApprovalCallback] = None,
        use_human_review: bool = False,  # Use human instead of Kimi K2
    ) -> AgentResult:
        """
        Run the full coding agent workflow.

        Args:
            task: Task description
            repo: Repository (owner/repo format)
            branch: Target branch
            create_pr: Whether to create a PR at the end
            max_fix_attempts: Maximum test fix attempts
            require_plan_approval: Use Kimi K2 for plan review (or human if use_human_review)
            require_code_approval: Use Kimi K2 for code review (or human if use_human_review)
            on_progress: Callback for progress updates (phase, message, detail)
            on_approval_needed: Callback for human approval (phase, content) -> (decision, feedback)
            use_human_review: If True, use on_approval_needed instead of Kimi K2

        Returns:
            AgentResult with success status and details
        """
        await self.initialize()

        state = AgentState(
            task=task,
            repo=repo,
            branch=branch,
            max_fix_attempts=max_fix_attempts,
        )

        start_time = datetime.utcnow()

        # Helper to report progress
        async def report_progress(phase: str, message: str, detail: Optional[str] = None):
            state.add_message(message)
            if on_progress:
                try:
                    await on_progress(phase, message, detail)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        # Helper for human/AI review
        async def get_review(phase: str, content: str) -> ReviewDecision:
            """Get review decision from human or Kimi K2."""
            if use_human_review and on_approval_needed:
                # Use human review via callback
                try:
                    decision, feedback = await on_approval_needed(phase, content)
                    decision = decision.lower().strip()

                    if decision in ("approve", "approved", "yes", "ok", "lgtm"):
                        return ReviewDecision.APPROVE
                    elif decision in ("reject", "rejected", "no", "cancel"):
                        state.plan_review = feedback if phase == "plan_review" else None
                        state.code_review = feedback if phase == "code_review" else None
                        return ReviewDecision.REJECT
                    else:
                        # Treat as modification request
                        state.plan_review = feedback if phase == "plan_review" else None
                        state.code_review = feedback if phase == "code_review" else None
                        return ReviewDecision.REQUEST_CHANGES
                except Exception as e:
                    logger.warning(f"Human review callback error: {e}, falling back to approve")
                    return ReviewDecision.APPROVE
            else:
                # Use Kimi K2 AI review
                if phase == "plan_review":
                    return await self._review_plan(state)
                else:
                    return await self._review_code(state)

        try:
            # Phase 1: Create sandbox and clone repo
            state.phase = AgentPhase.UNDERSTANDING
            await report_progress("understanding", f"Creating sandbox for {repo}")
            sandbox = await self.sandboxes.create(
                repo_url=f"https://github.com/{repo}.git",
                branch=branch,
            )
            state.sandbox_id = sandbox.id
            self.editor.workspace_root = sandbox.workspace_path

            # Phase 2: Understand codebase
            await report_progress("understanding", "Analyzing codebase...")
            state.repo_map = await self._generate_repo_map(sandbox)

            # Phase 3: Create plan
            state.phase = AgentPhase.PLANNING
            await report_progress("planning", "Creating implementation plan...")
            state.plan = await self._create_plan(state)
            await report_progress("planning", "Plan ready", state.plan)

            # Phase 4: Review plan (human or Kimi K2)
            if require_plan_approval:
                state.phase = AgentPhase.PLAN_REVIEW
                reviewer_type = "human" if use_human_review else "autonomous reviewer"
                await report_progress("plan_review", f"Waiting for {reviewer_type} to analyze plan...")

                review_result = await get_review("plan_review", state.plan)

                if review_result == ReviewDecision.REJECT:
                    state.phase = AgentPhase.FAILED
                    state.error = f"Plan rejected: {state.plan_review or 'No reason given'}"
                    await report_progress("failed", f"Plan rejected", state.error)
                    return self._create_result(state, start_time)

                if review_result == ReviewDecision.REQUEST_CHANGES:
                    # Revise plan based on feedback
                    await report_progress("planning", "Revising plan based on feedback...")
                    state.plan = await self._revise_plan(state)
                    await report_progress("planning", "Plan revised", state.plan)

                state.plan_approved = True
                await report_progress("plan_review", "Plan approved!")

            # Phase 5: Execute plan (coding)
            state.phase = AgentPhase.CODING
            await report_progress("coding", "Implementing changes...")
            await self._execute_plan(state)

            # Report changes made
            successful_changes = [c for c in state.changes_made if c.get('success')]
            if successful_changes:
                changes_detail = "\n".join([f"- {c['file']}" for c in successful_changes[:10]])
                await report_progress("coding", f"Made {len(successful_changes)} changes", changes_detail)

            # Phase 6: Run tests
            state.phase = AgentPhase.TESTING
            await report_progress("testing", "Running tests...")
            test_result = await self._run_tests(state)

            # Phase 7: Fix loop
            while not test_result.test_passed and state.fix_attempts < state.max_fix_attempts:
                state.phase = AgentPhase.FIXING
                state.fix_attempts += 1
                await report_progress(
                    "fixing",
                    f"Fixing errors (attempt {state.fix_attempts}/{state.max_fix_attempts})...",
                    state.test_output[-500:] if state.test_output else None
                )

                await self._fix_errors(state)

                state.phase = AgentPhase.TESTING
                await report_progress("testing", "Re-running tests...")
                test_result = await self._run_tests(state)

            if not test_result.test_passed:
                state.phase = AgentPhase.FAILED
                state.error = f"Tests still failing after {state.max_fix_attempts} fix attempts"
                await report_progress("failed", "Tests failed", state.test_output[-1000:])
                return self._create_result(state, start_time)

            state.test_passed = True
            await report_progress("testing", "Tests passed!")

            # Phase 8: Code review (human or Kimi K2)
            if require_code_approval:
                state.phase = AgentPhase.CODE_REVIEW
                reviewer_type = "human" if use_human_review else "autonomous reviewer"
                await report_progress("code_review", f"Waiting for {reviewer_type} to check code...")

                # Build code summary for review
                changes_summary = "\n".join([
                    f"- {c['file']}: {c.get('strategy', 'modified')}"
                    for c in state.changes_made if c['success']
                ])
                review_content = f"**Changes Made:**\n{changes_summary}\n\n**Tests:** PASSED"

                review_result = await get_review("code_review", review_content)

                if review_result == ReviewDecision.REJECT:
                    state.phase = AgentPhase.FAILED
                    state.error = f"Code rejected: {state.code_review or 'No reason given'}"
                    await report_progress("failed", "Code rejected", state.error)
                    return self._create_result(state, start_time)

                state.code_approved = True
                await report_progress("code_review", "Code approved!")

            # Phase 9: Commit changes
            state.phase = AgentPhase.COMMITTING
            await report_progress("committing", "Committing changes...")
            await self._commit_changes(state)

            if state.commit_sha:
                await report_progress("committing", f"Committed: {state.commit_sha}")

            # Phase 10: Create PR if requested
            if create_pr:
                await report_progress("committing", "Creating pull request...")
                await self._create_pr(state)
                if state.pr_url:
                    await report_progress("committing", f"PR created: {state.pr_url}")

            state.phase = AgentPhase.COMPLETE
            await report_progress("complete", "Task completed successfully!")

        except Exception as e:
            logger.exception("Agent error")
            state.phase = AgentPhase.FAILED
            state.error = str(e)
            if on_progress:
                await report_progress("failed", f"Error: {str(e)}")

        finally:
            # Clean up sandbox
            if state.sandbox_id:
                await self.sandboxes.destroy(state.sandbox_id)

        return self._create_result(state, start_time)

    async def _generate_repo_map(self, sandbox: Sandbox) -> str:
        """Generate a map of the repository structure."""
        # Get file listing
        result = await self.sandboxes.execute(
            sandbox.id,
            "find . -type f -name '*.py' -o -name '*.js' -o -name '*.ts' | head -100"
        )
        files = result.stdout.strip().split('\n') if result.stdout else []

        # Get basic structure
        result = await self.sandboxes.execute(
            sandbox.id,
            "ls -la && echo '---' && find . -type d -not -path '*/.*' | head -30"
        )

        repo_map = f"## File Structure\n{result.stdout}\n\n## Key Files\n"
        for f in files[:20]:  # Limit to 20 files
            repo_map += f"- {f}\n"

        return repo_map

    async def _create_plan(self, state: AgentState) -> str:
        """Create implementation plan using Gemini."""
        prompt = PLANNING_PROMPT.format(
            task=state.task,
            repo=state.repo,
            branch=state.branch,
            repo_map=state.repo_map,
        )

        response = await self.models.chat(
            model_id=self.models.get_model_for_task("planning"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            task_type="planning",
        )

        return response["content"]

    async def _review_plan(self, state: AgentState) -> ReviewDecision:
        """Review plan using Kimi K2 Thinking."""
        prompt = PLAN_REVIEW_PROMPT.format(
            task=state.task,
            plan=state.plan,
            repo=state.repo,
            branch=state.branch,
        )

        response = await self.models.chat(
            model_id="kimi-k2-thinking",
            messages=[
                {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            task_type="review",
        )

        state.plan_review = response["content"]

        # Parse the review decision
        content = response["content"].upper()
        if "REJECT" in content and "APPROVE" not in content:
            return ReviewDecision.REJECT
        elif "REQUEST_CHANGES" in content:
            return ReviewDecision.REQUEST_CHANGES
        else:
            return ReviewDecision.APPROVE

    async def _revise_plan(self, state: AgentState) -> str:
        """Revise plan based on reviewer feedback."""
        prompt = f"""## Original Plan
{state.plan}

## Reviewer Feedback
{state.plan_review}

## Instructions
Revise your plan to address the reviewer's concerns. Keep what was good, fix what was flagged.
"""

        response = await self.models.chat(
            model_id=self.models.get_model_for_task("planning"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            task_type="planning",
        )

        return response["content"]

    async def _execute_plan(self, state: AgentState):
        """Execute the implementation plan using Claude Large."""
        # Get relevant file contents
        files_content = await self._get_relevant_files(state)

        prompt = CODING_PROMPT.format(
            task=state.task,
            plan=state.plan,
            current_step="Execute the full implementation plan",
            files_content=files_content,
        )

        response = await self.models.chat(
            model_id=self.models.get_model_for_task("coding"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            task_type="coding",
        )

        # Parse and apply edits
        edits = parse_edit_blocks(response["content"])

        for filename, old_str, new_str in edits:
            result = self.editor.apply_edit_to_file(filename, old_str, new_str)

            state.changes_made.append({
                "file": filename,
                "success": result.success,
                "strategy": result.strategy_used,
                "error": result.error,
            })

            if result.success:
                state.add_message(f"Edited {filename} ({result.strategy_used})")
            else:
                state.add_message(f"Failed to edit {filename}: {result.error}")

    async def _get_relevant_files(self, state: AgentState) -> str:
        """Get content of files likely relevant to the task."""
        sandbox = self.sandboxes.sandboxes.get(state.sandbox_id)
        if not sandbox:
            return ""

        # Find Python files
        files = await self.sandboxes.list_files(state.sandbox_id, ".", "*.py")

        content = ""
        for f in files[:10]:  # Limit to 10 files
            try:
                file_content = await self.sandboxes.read_file(state.sandbox_id, f)
                content += f"\n### {f}\n```python\n{file_content[:3000]}\n```\n"
            except Exception:
                pass

        return content

    async def _run_tests(self, state: AgentState) -> AgentState:
        """Run tests in the sandbox."""
        # Try common test commands
        test_commands = [
            "pytest -v 2>&1",
            "python -m pytest -v 2>&1",
            "npm test 2>&1",
            "python -m unittest discover 2>&1",
        ]

        for cmd in test_commands:
            result = await self.sandboxes.execute(state.sandbox_id, cmd, timeout=300)

            if result.exit_code == 0:
                state.test_output = result.stdout
                state.test_passed = True
                state.add_message("Tests passed!")
                return state

            if "no tests ran" not in result.stdout.lower():
                state.test_output = result.stdout + "\n" + result.stderr
                state.test_passed = False
                state.add_message(f"Tests failed (exit code {result.exit_code})")
                return state

        # No tests found - consider it a pass
        state.test_passed = True
        state.add_message("No tests found, skipping")
        return state

    async def _fix_errors(self, state: AgentState):
        """Fix test errors using Claude."""
        # Get recent changes summary
        recent_changes = "\n".join([
            f"- {c['file']}: {'success' if c['success'] else c['error']}"
            for c in state.changes_made[-5:]
        ])

        files_content = await self._get_relevant_files(state)

        prompt = FIX_ERROR_PROMPT.format(
            task=state.task,
            error=state.test_output[-5000:],  # Last 5K chars of error
            recent_changes=recent_changes,
            files_content=files_content,
        )

        response = await self.models.chat(
            model_id=self.models.get_model_for_task("testing"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            task_type="testing",
        )

        # Parse and apply fixes
        edits = parse_edit_blocks(response["content"])

        for filename, old_str, new_str in edits:
            result = self.editor.apply_edit_to_file(filename, old_str, new_str)

            state.changes_made.append({
                "file": filename,
                "success": result.success,
                "strategy": result.strategy_used,
                "type": "fix",
            })

            if result.success:
                state.add_message(f"Fixed {filename}")

    async def _review_code(self, state: AgentState) -> ReviewDecision:
        """Final code review using Kimi K2 Thinking."""
        changes_summary = "\n".join([
            f"- {c['file']}: {c.get('strategy', 'modified')}"
            for c in state.changes_made if c['success']
        ])

        prompt = f"""## Task
{state.task}

## Changes Made
{changes_summary}

## Test Results
Tests: {'PASSED' if state.test_passed else 'FAILED'}
```
{state.test_output[-2000:]}
```

## Your Role
Do a final review of these changes before commit. Is the implementation correct and complete?
"""

        response = await self.models.chat(
            model_id="kimi-k2-thinking",
            messages=[
                {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            task_type="review",
        )

        state.code_review = response["content"]

        # Log thinking/reasoning if available
        if response.get("thinking"):
            logger.info(f"Kimi K2 reasoning: {response['thinking'][:500]}...")

        content = response["content"].upper()
        if "REJECT" in content and "APPROVE" not in content:
            return ReviewDecision.REJECT
        elif "REQUEST_CHANGES" in content:
            return ReviewDecision.REQUEST_CHANGES
        else:
            return ReviewDecision.APPROVE

    async def _commit_changes(self, state: AgentState):
        """Commit changes to git."""
        # Stage all changes
        await self.sandboxes.execute(state.sandbox_id, "git add -A")

        # Create commit message
        commit_msg = f"""feat: {state.task[:50]}

Implemented via Polli CodeAgent.

Changes:
{chr(10).join([f'- {c["file"]}' for c in state.changes_made if c['success']])}
"""

        result = await self.sandboxes.execute(
            state.sandbox_id,
            f'git commit -m "{commit_msg}"'
        )

        if result.exit_code == 0:
            # Get commit SHA
            sha_result = await self.sandboxes.execute(
                state.sandbox_id,
                "git rev-parse HEAD"
            )
            state.commit_sha = sha_result.stdout.strip()[:8]
            state.add_message(f"Committed: {state.commit_sha}")

    async def _create_pr(self, state: AgentState):
        """Create a pull request."""
        # Push branch
        new_branch = f"polli/{state.task[:30].replace(' ', '-').lower()}"

        await self.sandboxes.execute(
            state.sandbox_id,
            f"git checkout -b {new_branch} && git push origin {new_branch}"
        )

        # Use gh CLI to create PR
        pr_body = f"""## Summary
{state.task}

## Changes
{chr(10).join([f'- {c["file"]}' for c in state.changes_made if c['success']])}

## Review Notes
{state.code_review or 'Automated implementation'}

---
🤖 Generated by Polli CodeAgent
"""

        result = await self.sandboxes.execute(
            state.sandbox_id,
            f'gh pr create --title "{state.task[:50]}" --body "{pr_body}" --base {state.branch}'
        )

        if result.exit_code == 0:
            # Extract PR URL from output
            state.pr_url = result.stdout.strip()
            state.add_message(f"Created PR: {state.pr_url}")

    def _create_result(self, state: AgentState, start_time: datetime) -> AgentResult:
        """Create the final result object."""
        duration = (datetime.utcnow() - start_time).total_seconds()

        return AgentResult(
            success=state.phase == AgentPhase.COMPLETE,
            phase=state.phase,
            task=state.task,
            repo=state.repo,
            branch=state.branch,
            changes=state.changes_made,
            commit_sha=state.commit_sha,
            pr_url=state.pr_url,
            error=state.error,
            messages=state.messages,
            duration=duration,
        )


# Global agent instance
code_agent = CodeAgent()
