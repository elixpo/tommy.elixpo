"""Planning prompts for the coding agent."""

PLANNING_PROMPT = """## Task
{task}

## Repository Information
Repository: {repo}
Branch: {branch}

## Codebase Context
{repo_map}

## Instructions

Create a detailed implementation plan for this task.

Your plan should include:
1. **Understanding**: What the task requires and why
2. **Affected Files**: List files to create/modify
3. **Implementation Steps**: Ordered list of specific changes
4. **Testing Strategy**: How to verify the changes work
5. **Potential Risks**: Edge cases or issues to watch for

Be specific about:
- What functions/classes to add or modify
- What imports are needed
- What tests to add or update

Format your plan as structured markdown.
"""

PLAN_REVIEW_PROMPT = """## Task Being Planned
{task}

## Proposed Plan
{plan}

## Repository Context
Repository: {repo}
Branch: {branch}

## Your Role

You are reviewing this implementation plan before execution.

Evaluate:
1. Is the plan complete? Will it fully address the task?
2. Is the approach correct? Are there better alternatives?
3. Are there missing steps or considerations?
4. Is it too complex? Is there a simpler approach?
5. Are there potential issues that could arise?

Respond with your assessment. If you request changes, be specific about what needs to change.
"""
