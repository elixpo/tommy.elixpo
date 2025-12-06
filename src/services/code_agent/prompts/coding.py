"""Coding prompts for the coding agent."""

CODING_PROMPT = """## Task
{task}

## Implementation Plan
{plan}

## Current Step
{current_step}

## Files Context

{files_content}

## Instructions

Implement the current step according to the plan.

Use SEARCH/REPLACE blocks to edit files:
```
path/to/file.py
<<<<<<< SEARCH
existing code to find
=======
new code to replace with
>>>>>>> REPLACE
```

Rules:
- Match existing code EXACTLY (including whitespace)
- Include enough context for unique matches
- Create new files with empty SEARCH block
- You can make multiple edits in one response

After making changes, explain what you did and why.
"""

FIX_ERROR_PROMPT = """## Task Context
{task}

## Error Output
```
{error}
```

## Recent Changes
{recent_changes}

## Relevant Files
{files_content}

## Instructions

Analyze the error and fix it.

Think about:
1. What is the error telling us?
2. What caused it?
3. How should we fix it?

Make the minimal fix needed. Use SEARCH/REPLACE blocks.

If you need more information about a file, say so.
If you think the error is unrelated to our changes, explain why.
"""

TEST_ANALYSIS_PROMPT = """## Test Output
```
{test_output}
```

## Exit Code: {exit_code}

## Instructions

Analyze the test results:
1. Did tests pass or fail?
2. If failed, which tests and why?
3. Are failures related to our changes?
4. What's the fix?

If all tests passed, confirm success.
If tests failed, identify the issue and propose a fix.
"""

CODE_REVIEW_PROMPT = """## Task
{task}

## Changes Made

{changes}

## Test Results
{test_results}

## Your Role

You are doing a final code review before commit.

Review for:
1. **Correctness**: Do changes implement the task correctly?
2. **Quality**: Is the code clean and maintainable?
3. **Completeness**: Is anything missing?
4. **Safety**: Are there security or stability concerns?

Respond with your assessment.
"""
