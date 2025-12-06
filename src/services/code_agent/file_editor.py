"""
SEARCH/REPLACE file editor inspired by Aider and OpenHands.

Provides robust file editing with multiple fallback strategies:
1. Exact match
2. Whitespace-flexible match
3. Fuzzy matching with similarity threshold

This is the core editing mechanism used by the coding agent.
"""

import re
import difflib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from difflib import SequenceMatcher


@dataclass
class EditResult:
    """Result of an edit operation."""
    success: bool
    new_content: Optional[str] = None
    error: Optional[str] = None
    matched_content: Optional[str] = None
    strategy_used: Optional[str] = None


class FileEditor:
    """
    SEARCH/REPLACE style file editor.

    Supports multiple edit strategies with graceful fallback:
    1. exact - Exact string match
    2. whitespace - Flexible whitespace matching
    3. fuzzy - Similarity-based matching (threshold: 0.8)
    """

    SIMILARITY_THRESHOLD = 0.8

    def __init__(self, workspace_root: str = "/workspace"):
        self.workspace_root = Path(workspace_root)
        self.edit_history: dict[str, list[str]] = {}  # path -> list of previous contents

    def validate_edit(self, content: str, old_str: str) -> tuple[bool, str, int]:
        """
        Validate that old_str exists and is unique in content.

        Returns:
            (is_valid, message, match_count)
        """
        if not old_str.strip():
            return True, "Empty old_str - will append", 0

        count = content.count(old_str)

        if count == 0:
            # Try to find similar content
            similar = self._find_similar_lines(old_str, content)
            if similar:
                return False, f"No exact match found. Did you mean:\n```\n{similar}\n```", 0
            return False, "No match found for the SEARCH block", 0

        if count > 1:
            return False, f"SEARCH block matches {count} locations. Add more context to make it unique.", count

        return True, "Valid - unique match found", 1

    def edit(self, content: str, old_str: str, new_str: str) -> EditResult:
        """
        Apply SEARCH/REPLACE edit with fallback strategies.

        Args:
            content: Current file content
            old_str: Text to search for (SEARCH block)
            new_str: Text to replace with (REPLACE block)

        Returns:
            EditResult with success status and new content or error
        """
        # Handle empty old_str as append
        if not old_str.strip():
            return EditResult(
                success=True,
                new_content=content + new_str,
                strategy_used="append"
            )

        # Strategy 1: Exact match
        result = self._exact_replace(content, old_str, new_str)
        if result.success:
            result.strategy_used = "exact"
            return result

        # Strategy 2: Whitespace-flexible match
        result = self._whitespace_flexible_replace(content, old_str, new_str)
        if result.success:
            result.strategy_used = "whitespace"
            return result

        # Strategy 3: Fuzzy match
        result = self._fuzzy_replace(content, old_str, new_str)
        if result.success:
            result.strategy_used = "fuzzy"
            return result

        # All strategies failed
        similar = self._find_similar_lines(old_str, content)
        error_msg = "SEARCH block not found in file."
        if similar:
            error_msg += f"\n\nDid you mean:\n```\n{similar}\n```"

        return EditResult(success=False, error=error_msg)

    def _exact_replace(self, content: str, old_str: str, new_str: str) -> EditResult:
        """Exact string replacement."""
        if old_str not in content:
            return EditResult(success=False, error="No exact match")

        count = content.count(old_str)
        if count > 1:
            return EditResult(
                success=False,
                error=f"Multiple matches ({count}). Add more context."
            )

        new_content = content.replace(old_str, new_str, 1)
        return EditResult(success=True, new_content=new_content)

    def _whitespace_flexible_replace(self, content: str, old_str: str, new_str: str) -> EditResult:
        """
        Match ignoring leading whitespace differences.
        Handles cases where LLM gets indentation slightly wrong.
        """
        content_lines = content.splitlines(keepends=True)
        old_lines = old_str.splitlines(keepends=True)
        new_lines = new_str.splitlines(keepends=True)

        if not old_lines:
            return EditResult(success=False, error="Empty search block")

        # Calculate minimum indentation in old_str
        min_indent = float('inf')
        for line in old_lines:
            if line.strip():
                indent = len(line) - len(line.lstrip())
                min_indent = min(min_indent, indent)

        if min_indent == float('inf'):
            min_indent = 0

        # Strip common indentation from old_lines for comparison
        normalized_old = []
        for line in old_lines:
            if line.strip():
                normalized_old.append(line[min_indent:] if len(line) > min_indent else line)
            else:
                normalized_old.append(line)

        # Search for matching block in content
        for i in range(len(content_lines) - len(old_lines) + 1):
            chunk = content_lines[i:i + len(old_lines)]

            # Check if chunk matches after stripping leading whitespace
            match_indent = self._get_matching_indent(chunk, normalized_old)
            if match_indent is not None:
                # Apply the found indentation to new_lines
                adjusted_new = []
                for line in new_lines:
                    if line.strip():
                        # Add the indentation from the matched content
                        adjusted_new.append(match_indent + line.lstrip())
                    else:
                        adjusted_new.append(line)

                # Replace the block
                result_lines = content_lines[:i] + adjusted_new + content_lines[i + len(old_lines):]
                return EditResult(success=True, new_content=''.join(result_lines))

        return EditResult(success=False, error="No whitespace-flexible match")

    def _get_matching_indent(self, chunk: list[str], normalized: list[str]) -> Optional[str]:
        """
        Check if chunk matches normalized lines (ignoring leading whitespace).
        Returns the common indent if match, None otherwise.
        """
        if len(chunk) != len(normalized):
            return None

        indent = None
        for c_line, n_line in zip(chunk, normalized):
            c_stripped = c_line.lstrip()
            n_stripped = n_line.lstrip()

            # Both empty is fine
            if not c_stripped and not n_stripped:
                continue

            # Content must match after stripping
            if c_stripped != n_stripped:
                return None

            # Track indent from first non-empty line
            if indent is None and c_stripped:
                c_indent = len(c_line) - len(c_stripped)
                n_indent = len(n_line) - len(n_stripped)
                indent = c_line[:c_indent - n_indent] if c_indent >= n_indent else ""

        return indent if indent is not None else ""

    def _fuzzy_replace(self, content: str, old_str: str, new_str: str) -> EditResult:
        """
        Fuzzy matching using sequence similarity.
        Only used as last resort with high threshold.
        """
        content_lines = content.splitlines(keepends=True)
        old_lines = old_str.splitlines(keepends=True)
        new_lines = new_str.splitlines(keepends=True)

        best_ratio = 0
        best_start = -1
        best_end = -1

        # Search with some flexibility in block size
        for length in range(len(old_lines) - 1, len(old_lines) + 2):
            if length <= 0 or length > len(content_lines):
                continue

            for i in range(len(content_lines) - length + 1):
                chunk = ''.join(content_lines[i:i + length])
                ratio = SequenceMatcher(None, chunk, old_str).ratio()

                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = i
                    best_end = i + length

        if best_ratio >= self.SIMILARITY_THRESHOLD:
            matched = ''.join(content_lines[best_start:best_end])
            result_lines = content_lines[:best_start] + new_lines + content_lines[best_end:]
            return EditResult(
                success=True,
                new_content=''.join(result_lines),
                matched_content=matched
            )

        return EditResult(success=False, error=f"Best fuzzy match: {best_ratio:.1%} (threshold: {self.SIMILARITY_THRESHOLD:.0%})")

    def _find_similar_lines(self, search: str, content: str, threshold: float = 0.6) -> Optional[str]:
        """Find similar lines in content to help user fix their SEARCH block."""
        search_lines = search.splitlines()
        content_lines = content.splitlines()

        if not search_lines:
            return None

        best_ratio = 0
        best_match = None
        best_idx = 0

        for i in range(len(content_lines) - len(search_lines) + 1):
            chunk = content_lines[i:i + len(search_lines)]
            ratio = SequenceMatcher(None, search_lines, chunk).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_match = chunk
                best_idx = i

        if best_ratio >= threshold and best_match:
            # Include some context
            start = max(0, best_idx - 2)
            end = min(len(content_lines), best_idx + len(search_lines) + 2)
            return '\n'.join(content_lines[start:end])

        return None

    def apply_edit_to_file(self, file_path: str, old_str: str, new_str: str) -> EditResult:
        """
        Apply edit to a file on disk.
        Maintains edit history for undo support.
        """
        path = Path(file_path)
        if not path.is_absolute():
            path = self.workspace_root / path

        if not path.exists():
            # Creating new file
            if old_str.strip():
                return EditResult(success=False, error=f"File {path} does not exist")

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_str)
            self.edit_history[str(path)] = [""]
            return EditResult(success=True, new_content=new_str, strategy_used="create")

        content = path.read_text()

        # Save to history for undo
        if str(path) not in self.edit_history:
            self.edit_history[str(path)] = []
        self.edit_history[str(path)].append(content)

        result = self.edit(content, old_str, new_str)

        if result.success and result.new_content is not None:
            path.write_text(result.new_content)

        return result

    def undo_edit(self, file_path: str) -> EditResult:
        """Undo the last edit to a file."""
        path = Path(file_path)
        if not path.is_absolute():
            path = self.workspace_root / path

        history = self.edit_history.get(str(path), [])
        if not history:
            return EditResult(success=False, error="No edit history for this file")

        previous_content = history.pop()
        path.write_text(previous_content)

        return EditResult(success=True, new_content=previous_content, strategy_used="undo")

    def view_file(self, file_path: str, start_line: int = 1, end_line: int = -1) -> str:
        """
        View file contents with line numbers.

        Args:
            file_path: Path to file
            start_line: Starting line (1-indexed)
            end_line: Ending line (-1 for end of file)
        """
        path = Path(file_path)
        if not path.is_absolute():
            path = self.workspace_root / path

        if not path.exists():
            return f"Error: File {path} does not exist"

        content = path.read_text()
        lines = content.splitlines()

        if end_line == -1:
            end_line = len(lines)

        # Clamp to valid range
        start_line = max(1, start_line)
        end_line = min(len(lines), end_line)

        # Format with line numbers
        output_lines = []
        for i, line in enumerate(lines[start_line - 1:end_line], start=start_line):
            output_lines.append(f"{i:6}\t{line}")

        return '\n'.join(output_lines)

    def create_file(self, file_path: str, content: str) -> EditResult:
        """Create a new file with the given content."""
        path = Path(file_path)
        if not path.is_absolute():
            path = self.workspace_root / path

        if path.exists():
            return EditResult(success=False, error=f"File {path} already exists. Use edit instead.")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.edit_history[str(path)] = [""]

        return EditResult(success=True, new_content=content, strategy_used="create")


# Parse SEARCH/REPLACE blocks from LLM output
def parse_edit_blocks(text: str) -> list[tuple[str, str, str]]:
    """
    Parse SEARCH/REPLACE blocks from text.

    Format:
    filename.py
    <<<<<<< SEARCH
    old content
    =======
    new content
    >>>>>>> REPLACE

    Returns:
        List of (filename, old_str, new_str) tuples
    """
    HEAD = r"^<{5,9} SEARCH>?\s*$"
    DIVIDER = r"^={5,9}\s*$"
    UPDATED = r"^>{5,9} REPLACE\s*$"

    head_pattern = re.compile(HEAD, re.MULTILINE)
    divider_pattern = re.compile(DIVIDER, re.MULTILINE)
    updated_pattern = re.compile(UPDATED, re.MULTILINE)

    lines = text.splitlines(keepends=True)
    edits = []
    i = 0
    current_filename = None

    while i < len(lines):
        line = lines[i]

        # Look for filename before SEARCH block
        if head_pattern.match(line.strip()):
            # Find filename in preceding lines
            for j in range(max(0, i - 3), i):
                candidate = lines[j].strip().rstrip(':').strip('`').strip('#').strip()
                if candidate and ('.' in candidate or '/' in candidate):
                    current_filename = candidate
                    break

            if not current_filename:
                i += 1
                continue

            # Collect SEARCH content
            original_text = []
            i += 1
            while i < len(lines) and not divider_pattern.match(lines[i].strip()):
                original_text.append(lines[i])
                i += 1

            if i >= len(lines):
                break

            # Collect REPLACE content
            updated_text = []
            i += 1
            while i < len(lines) and not updated_pattern.match(lines[i].strip()):
                updated_text.append(lines[i])
                i += 1

            edits.append((
                current_filename,
                ''.join(original_text),
                ''.join(updated_text)
            ))

        i += 1

    return edits
