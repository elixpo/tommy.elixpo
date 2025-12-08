"""
Discord embed builder for code task progress updates.

Creates a single embed that updates in real-time with:
- Task header (issue title, PR title, etc.)
- Checklist of steps (✅ 🔄 ⬜)
- Current status message
- Footer with elapsed time
- Close Terminal button (for thread-based tasks)
"""

import asyncio
import discord
from discord.ui import View, Button
from datetime import datetime
from typing import Optional, List, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# PERSISTENT TERMINAL BUTTON VIEWS
# =============================================================================
# These views survive bot restarts by:
# 1. Using custom_id patterns that encode thread_id and user_id
# 2. Being registered on bot startup with add_view()
# 3. Looking up terminal info from sandbox at click time

# Type for the callback function
CloseTerminalCallback = Callable[[str], Awaitable[bool]]

# Global reference to sandbox getter (set by bot on startup)
_get_sandbox_func: Optional[Callable] = None


def set_sandbox_getter(getter: Callable):
    """Set the sandbox getter function. Called by bot on startup."""
    global _get_sandbox_func
    _get_sandbox_func = getter


class PersistentCloseTerminalView(View):
    """
    Persistent view for Close Terminal button.

    Survives bot restarts by:
    - Using custom_id pattern: close_terminal:{thread_id}:{user_id}
    - Being registered on bot startup
    - Looking up terminal from sandbox at click time
    """

    def __init__(self):
        super().__init__(timeout=None)  # No timeout - persists forever

    @discord.ui.button(
        label="Close Terminal",
        style=discord.ButtonStyle.secondary,
        emoji="🔒",
        custom_id="persistent_close_terminal",
    )
    async def close_terminal_button(
        self, interaction: discord.Interaction, button: Button
    ):
        """Handle the Close Terminal button click."""
        # Get thread_id from the channel where button was clicked
        thread_id = str(interaction.channel_id)

        # Look up terminal info from sandbox
        if _get_sandbox_func is None:
            await interaction.response.send_message(
                "Terminal system not initialized. Please try again.",
                ephemeral=True,
            )
            return

        sandbox = _get_sandbox_func()
        terminal_info = sandbox.get_terminal_info(thread_id)

        if not terminal_info:
            # Terminal already closed or doesn't exist
            await interaction.response.send_message(
                "Terminal session not found. It may already be closed.",
                ephemeral=True,
            )
            # Disable the button since terminal doesn't exist
            button.disabled = True
            button.label = "Terminal Closed"
            button.emoji = "✅"
            try:
                await interaction.message.edit(view=self)
            except:
                pass
            return

        owner_user_id = terminal_info.get("user_id")

        # Verify user is the owner
        if owner_user_id and interaction.user.id != owner_user_id:
            await interaction.response.send_message(
                f"Only <@{owner_user_id}> can close this terminal session.",
                ephemeral=True,
            )
            return

        # Defer response (closing might take a moment)
        await interaction.response.defer(ephemeral=True)

        try:
            # Close the terminal
            success = await sandbox.close_thread_terminal(thread_id)

            if success:
                # Disable the button
                button.disabled = True
                button.label = "Terminal Closed"
                button.emoji = "✅"
                await interaction.message.edit(view=self)

                await interaction.followup.send(
                    "Terminal session closed successfully.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Failed to close terminal session. It may already be closed.",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error closing terminal {thread_id}: {e}")
            await interaction.followup.send(
                f"Error closing terminal: {str(e)[:100]}",
                ephemeral=True,
            )


class PersistentStaleTerminalView(View):
    """
    Persistent view for stale terminal notification.

    Survives bot restarts. The thread_id is derived from channel_id.
    """

    def __init__(self):
        super().__init__(timeout=None)  # No timeout - persists forever

    @discord.ui.button(
        label="Keep Open",
        style=discord.ButtonStyle.primary,
        emoji="▶️",
        custom_id="persistent_keep_terminal",
    )
    async def keep_terminal_button(
        self, interaction: discord.Interaction, button: Button
    ):
        """User wants to keep the terminal open."""
        thread_id = str(interaction.channel_id)

        if _get_sandbox_func is None:
            await interaction.response.send_message(
                "Terminal system not initialized.",
                ephemeral=True,
            )
            return

        sandbox = _get_sandbox_func()
        terminal_info = sandbox.get_terminal_info(thread_id)

        if not terminal_info:
            await interaction.response.send_message(
                "Terminal session not found.",
                ephemeral=True,
            )
            return

        owner_user_id = terminal_info.get("user_id")

        if owner_user_id and interaction.user.id != owner_user_id:
            await interaction.response.send_message(
                f"Only <@{owner_user_id}> can manage this terminal.",
                ephemeral=True,
            )
            return

        # Reset the stale notification flag
        sandbox.reset_stale_notification(thread_id)

        # Disable buttons
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=f"<@{interaction.user.id}> Terminal kept open. Will check again in 1 hour.",
            view=self,
        )

    @discord.ui.button(
        label="Close Terminal",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="persistent_close_stale_terminal",
    )
    async def close_terminal_button(
        self, interaction: discord.Interaction, button: Button
    ):
        """User wants to close the terminal."""
        thread_id = str(interaction.channel_id)

        if _get_sandbox_func is None:
            await interaction.response.send_message(
                "Terminal system not initialized.",
                ephemeral=True,
            )
            return

        sandbox = _get_sandbox_func()
        terminal_info = sandbox.get_terminal_info(thread_id)

        if not terminal_info:
            # Terminal already gone - update UI
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content=f"<@{interaction.user.id}> Terminal session already closed.",
                view=self,
            )
            return

        owner_user_id = terminal_info.get("user_id")

        if owner_user_id and interaction.user.id != owner_user_id:
            await interaction.response.send_message(
                f"Only <@{owner_user_id}> can manage this terminal.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            success = await sandbox.close_thread_terminal(thread_id)

            # Disable buttons
            for child in self.children:
                child.disabled = True

            if success:
                await interaction.message.edit(
                    content=f"<@{interaction.user.id}> Terminal session closed.",
                    view=self,
                )
            else:
                await interaction.message.edit(
                    content=f"<@{interaction.user.id}> Failed to close terminal (may already be closed).",
                    view=self,
                )

        except Exception as e:
            logger.error(f"Error closing stale terminal {thread_id}: {e}")
            await interaction.followup.send(
                f"Error: {str(e)[:100]}",
                ephemeral=True,
            )


# =============================================================================
# LEGACY CLOSE TERMINAL BUTTON VIEW (kept for compatibility)
# =============================================================================

class CloseTerminalView(View):
    """
    Discord View with a "Close Terminal" button.

    Only the user who started the terminal (owner_user_id) can click the button.
    This view is added to the final task completion embed when there are git changes.
    """

    def __init__(
        self,
        thread_id: str,
        owner_user_id: int,
        close_callback: CloseTerminalCallback,
        timeout: Optional[float] = None,  # No timeout - button stays forever
    ):
        """
        Args:
            thread_id: Discord thread ID (the universal key)
            owner_user_id: Discord user ID who started the terminal (only they can close it)
            close_callback: Async function to call when closing: callback(thread_id) -> success
            timeout: Optional timeout for the view (None = no timeout)
        """
        super().__init__(timeout=timeout)
        self.thread_id = thread_id
        self.owner_user_id = owner_user_id
        self.close_callback = close_callback
        self._closed = False

    @discord.ui.button(
        label="Close Terminal",
        style=discord.ButtonStyle.secondary,
        emoji="🔒",
        custom_id="close_terminal",
    )
    async def close_terminal_button(
        self, interaction: discord.Interaction, button: Button
    ):
        """Handle the Close Terminal button click."""
        # Verify user is the owner
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message(
                f"Only <@{self.owner_user_id}> can close this terminal session.",
                ephemeral=True,
            )
            return

        # Already closed?
        if self._closed:
            await interaction.response.send_message(
                "Terminal session already closed.",
                ephemeral=True,
            )
            return

        # Defer response (closing might take a moment)
        await interaction.response.defer(ephemeral=True)

        try:
            # Call the close callback
            success = await self.close_callback(self.thread_id)

            if success:
                self._closed = True
                # Disable the button
                button.disabled = True
                button.label = "Terminal Closed"
                button.emoji = "✅"
                await interaction.message.edit(view=self)

                await interaction.followup.send(
                    "Terminal session closed successfully.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "Failed to close terminal session. It may already be closed.",
                    ephemeral=True,
                )

        except Exception as e:
            logger.error(f"Error closing terminal {self.thread_id}: {e}")
            await interaction.followup.send(
                f"Error closing terminal: {str(e)[:100]}",
                ephemeral=True,
            )


# Type for reset stale callback
ResetStaleCallback = Callable[[str], None]


class StaleTerminalView(View):
    """
    Discord View for stale terminal notification.

    Shown after 1 hour of inactivity to ask user if they want to keep or close.
    """

    def __init__(
        self,
        thread_id: str,
        owner_user_id: int,
        close_callback: CloseTerminalCallback,
        reset_stale_callback: Optional[ResetStaleCallback] = None,
        timeout: float = 3600,  # 1 hour timeout for this notification
    ):
        super().__init__(timeout=timeout)
        self.thread_id = thread_id
        self.owner_user_id = owner_user_id
        self.close_callback = close_callback
        self.reset_stale_callback = reset_stale_callback
        self._handled = False

    @discord.ui.button(
        label="Keep Open",
        style=discord.ButtonStyle.primary,
        emoji="▶️",
        custom_id="keep_terminal",
    )
    async def keep_terminal_button(
        self, interaction: discord.Interaction, button: Button
    ):
        """User wants to keep the terminal open."""
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message(
                f"Only <@{self.owner_user_id}> can manage this terminal.",
                ephemeral=True,
            )
            return

        if self._handled:
            await interaction.response.send_message(
                "Already handled.",
                ephemeral=True,
            )
            return

        self._handled = True

        # Reset the stale notification flag so user gets notified again after another hour
        if self.reset_stale_callback:
            self.reset_stale_callback(self.thread_id)

        # Disable buttons
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=f"<@{self.owner_user_id}> Terminal kept open. Will check again in 1 hour.",
            view=self,
        )

    @discord.ui.button(
        label="Close Terminal",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="close_stale_terminal",
    )
    async def close_terminal_button(
        self, interaction: discord.Interaction, button: Button
    ):
        """User wants to close the terminal."""
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message(
                f"Only <@{self.owner_user_id}> can manage this terminal.",
                ephemeral=True,
            )
            return

        if self._handled:
            await interaction.response.send_message(
                "Already handled.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            success = await self.close_callback(self.thread_id)
            self._handled = True

            # Disable buttons
            for child in self.children:
                child.disabled = True

            if success:
                await interaction.message.edit(
                    content=f"<@{self.owner_user_id}> Terminal session closed.",
                    view=self,
                )
            else:
                await interaction.message.edit(
                    content=f"<@{self.owner_user_id}> Failed to close terminal (may already be closed).",
                    view=self,
                )

        except Exception as e:
            logger.error(f"Error closing stale terminal {self.thread_id}: {e}")
            await interaction.followup.send(
                f"Error: {str(e)[:100]}",
                ephemeral=True,
            )

    async def on_timeout(self):
        """Called when the view times out (after 1 hour of no interaction)."""
        # The stale check loop will handle re-notifying
        pass


# =============================================================================
# PROGRESS EMBED - CLAUDE CODE STYLE
# =============================================================================

class StepStatus(Enum):
    """Status of a todo item."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TodoItem:
    """A todo item in the progress list."""
    content: str
    status: StepStatus = StepStatus.PENDING

    def to_string(self) -> str:
        """Convert to Claude Code style string."""
        # Claude Code style: ☐ pending, ◉ in progress, ✓ done, ✗ failed
        emoji_map = {
            StepStatus.PENDING: "☐",
            StepStatus.IN_PROGRESS: "◉",
            StepStatus.COMPLETED: "✓",
            StepStatus.FAILED: "✗",
        }
        emoji = emoji_map.get(self.status, "☐")
        return f"{emoji} {self.content}"


@dataclass
class ProgressEmbed:
    """
    Live progress embed for polly agent tasks.

    Shows real-time status:
    - Title: Current action with spinner (✻ Working on X...)
    - Progress: Todo list with nested sub-actions
    - Files: Files being modified (updated live)
    - Branch: Git branch info
    - Footer: Elapsed time

    Usage:
        embed = ProgressEmbed(current_action="Analyzing code")
        embed.add_todo("Read the file")
        embed.set_sub_action("Reading src/app.py")
        embed.add_file("src/app.py")

        discord_embed = embed.build()
    """

    current_action: str = "Working..."
    todos: List[TodoItem] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    color: int = 0x5865F2  # Discord blurple

    # State
    is_complete: bool = False
    is_failed: bool = False

    # Live status fields
    sub_action: str = ""  # Nested action under current todo (e.g., "Reading file X")
    files_changed: List[str] = field(default_factory=list)  # Files being modified
    branch_name: str = ""  # Git branch
    base_branch: str = "main"  # Target branch for PR
    queue_position: int = 0  # Position in task queue (0 = running)

    # Legacy compatibility
    title: str = ""
    description: str = ""
    status_message: str = ""
    issue_url: Optional[str] = None
    pr_url: Optional[str] = None
    repo_url: Optional[str] = None

    @property
    def steps(self) -> List[TodoItem]:
        """Alias for todos (backward compatibility)."""
        return self.todos

    def add_todo(self, content: str) -> int:
        """Add a todo item. Returns index."""
        self.todos.append(TodoItem(content=content))
        return len(self.todos) - 1

    def add_step(self, name: str, details: Optional[str] = None) -> int:
        """Backward compatible alias for add_todo."""
        return self.add_todo(name)

    def start_todo(self, index: int):
        """Mark a todo as in progress and update current action."""
        if 0 <= index < len(self.todos):
            self.todos[index].status = StepStatus.IN_PROGRESS
            self.current_action = self.todos[index].content

    def start_step(self, index: int, details: Optional[str] = None):
        """Backward compatible alias."""
        self.start_todo(index)

    def complete_todo(self, index: int):
        """Mark a todo as completed."""
        if 0 <= index < len(self.todos):
            self.todos[index].status = StepStatus.COMPLETED

    def complete_step(self, index: int, details: Optional[str] = None):
        """Backward compatible alias."""
        self.complete_todo(index)

    def fail_todo(self, index: int):
        """Mark a todo as failed."""
        if 0 <= index < len(self.todos):
            self.todos[index].status = StepStatus.FAILED

    def fail_step(self, index: int, details: Optional[str] = None):
        """Backward compatible alias."""
        self.fail_todo(index)

    def skip_step(self, index: int, details: Optional[str] = None):
        """Mark as completed (no skip in Claude Code style)."""
        self.complete_todo(index)

    def set_action(self, action: str):
        """Set the current action shown in title."""
        self.current_action = action
        self.sub_action = ""  # Clear sub-action when main action changes

    def set_sub_action(self, sub_action: str):
        """Set a nested sub-action (shown under current todo)."""
        self.sub_action = sub_action

    def set_status(self, message: str):
        """Backward compatible - sets current action."""
        self.current_action = message

    def add_file(self, file_path: str):
        """Add a file to the files changed list."""
        if file_path and file_path not in self.files_changed:
            self.files_changed.append(file_path)

    def set_files(self, files: List[str]):
        """Set all files changed at once."""
        self.files_changed = list(files) if files else []

    def set_branch(self, branch_name: str, base_branch: str = "main"):
        """Set the git branch info."""
        self.branch_name = branch_name
        self.base_branch = base_branch

    def set_queue_position(self, position: int):
        """Set queue position (0 = running, >0 = waiting)."""
        self.queue_position = position

    def mark_complete(self, success: bool = True):
        """Mark the task as complete."""
        self.is_complete = True
        self.is_failed = not success
        self.color = 0x57F287 if success else 0xED4245  # Green or red

    def elapsed_time(self) -> str:
        """Get formatted elapsed time."""
        seconds = int((datetime.utcnow() - self.started_at).total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        secs = seconds % 60
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"

    def _get_title(self) -> str:
        """Build the title with spinner/status."""
        if self.is_complete:
            emoji = "✓" if not self.is_failed else "✗"
            status = "Done" if not self.is_failed else "Failed"
            return f"{emoji} {status}"
        else:
            # Spinner + current action
            return f"✻ {self.current_action}…"

    def build(self) -> discord.Embed:
        """Build the Discord embed object with live status."""
        embed = discord.Embed(
            title=self._get_title(),
            color=self.color,
        )

        # Build description with all sections
        sections = []

        # Queue status (if waiting)
        if self.queue_position > 0:
            sections.append(f"⏳ **Queue position:** #{self.queue_position}")
            sections.append("")

        # Todo list with sub-action
        if self.todos:
            todo_lines = []
            for i, todo in enumerate(self.todos):
                todo_lines.append(todo.to_string())
                # Show sub-action under the in-progress todo
                if todo.status == StepStatus.IN_PROGRESS and self.sub_action:
                    todo_lines.append(f"   └─ {self.sub_action}")
            sections.append("\n".join(todo_lines))

        # Files changed section
        if self.files_changed:
            sections.append("")
            file_count = len(self.files_changed)
            sections.append(f"📁 **Files** ({file_count})")
            # Show up to 8 files, then summarize
            for f in self.files_changed[:8]:
                # Shorten long paths
                display_path = f if len(f) < 40 else "…" + f[-38:]
                sections.append(f"  • `{display_path}`")
            if file_count > 8:
                sections.append(f"  • *+{file_count - 8} more*")

        embed.description = "\n".join(sections) if sections else None

        # Footer with branch info and time
        footer_parts = []
        if self.branch_name:
            footer_parts.append(f"🔀 {self.branch_name} → {self.base_branch}")
        footer_parts.append(f"⏱ {self.elapsed_time()}")
        embed.set_footer(text="  │  ".join(footer_parts))

        return embed


class ProgressEmbedManager:
    """
    Manages a progress embed with Discord message updates.

    Usage:
        manager = ProgressEmbedManager(channel)
        await manager.start(title="Fixing Issue #5735", description="Bug in URL encoding")

        manager.add_step("Analyze")
        manager.add_step("Fix")
        manager.add_step("Test")
        await manager.update()

        manager.complete_step(0)
        manager.start_step(1)
        manager.set_status("Found the bug!")
        await manager.update()
    """

    def __init__(self, channel: discord.TextChannel):
        self.channel = channel
        self.message: Optional[discord.Message] = None
        self.embed: Optional[ProgressEmbed] = None
        self._update_lock = asyncio.Lock()
        self._last_update: datetime = datetime.utcnow()
        self._min_update_interval = 1.0  # Minimum seconds between updates

    async def start(
        self,
        title: str = "",
        description: str = "",
        issue_url: Optional[str] = None,
        repo_url: Optional[str] = None,
        current_action: str = "Starting...",
    ) -> discord.Message:
        """Create and send the initial embed."""
        self.embed = ProgressEmbed(
            current_action=current_action or title or "Working...",
        )

        discord_embed = self.embed.build()
        self.message = await self.channel.send(embed=discord_embed)
        return self.message

    def set_action(self, action: str):
        """Set the current action (shown in title)."""
        if self.embed:
            self.embed.set_action(action)

    def add_step(self, name: str, details: Optional[str] = None) -> int:
        """Add a step. Returns step index."""
        if self.embed:
            return self.embed.add_step(name, details)
        return -1

    def start_step(self, index: int, details: Optional[str] = None):
        """Mark step as in progress."""
        if self.embed:
            self.embed.start_step(index, details)

    def complete_step(self, index: int, details: Optional[str] = None):
        """Mark step as completed."""
        if self.embed:
            self.embed.complete_step(index, details)

    def fail_step(self, index: int, details: Optional[str] = None):
        """Mark step as failed."""
        if self.embed:
            self.embed.fail_step(index, details)

    def set_status(self, message: str):
        """Set status message."""
        if self.embed:
            self.embed.set_status(message)

    def set_sub_action(self, sub_action: str):
        """Set nested sub-action under current step."""
        if self.embed:
            self.embed.set_sub_action(sub_action)

    def add_file(self, file_path: str):
        """Add a file to the changed files list."""
        if self.embed:
            self.embed.add_file(file_path)

    def set_files(self, files: List[str]):
        """Set all files changed at once."""
        if self.embed:
            self.embed.set_files(files)

    def set_branch(self, branch_name: str, base_branch: str = "main"):
        """Set git branch info."""
        if self.embed:
            self.embed.set_branch(branch_name, base_branch)

    def set_queue_position(self, position: int):
        """Set queue position."""
        if self.embed:
            self.embed.set_queue_position(position)

    def set_pr_url(self, url: str):
        """Set the PR URL."""
        if self.embed:
            self.embed.pr_url = url

    def mark_complete(self, success: bool = True):
        """Mark task as complete."""
        if self.embed:
            self.embed.mark_complete(success)

    async def update(self, force: bool = False):
        """
        Update the Discord message with current embed state.

        Throttles updates to avoid rate limiting.
        """
        if not self.message or not self.embed:
            return

        async with self._update_lock:
            # Throttle updates
            now = datetime.utcnow()
            elapsed = (now - self._last_update).total_seconds()
            if not force and elapsed < self._min_update_interval:
                return

            try:
                discord_embed = self.embed.build()
                await self.message.edit(embed=discord_embed)
                self._last_update = now
            except discord.HTTPException as e:
                # Log but don't crash on rate limits
                logging.getLogger(__name__).warning(f"Failed to update embed: {e}")

    async def finish(
        self,
        success: bool = True,
        final_status: Optional[str] = None,
        view: Optional[View] = None,
    ):
        """
        Mark complete and do final update.

        Args:
            success: Whether task completed successfully
            final_status: Final status message to display
            view: Optional Discord View (e.g., CloseTerminalView) to attach to the message
        """
        if self.embed:
            self.embed.mark_complete(success)
            if final_status:
                self.embed.set_status(final_status)

        if not self.message or not self.embed:
            return

        async with self._update_lock:
            try:
                discord_embed = self.embed.build()
                if view:
                    await self.message.edit(embed=discord_embed, view=view)
                else:
                    await self.message.edit(embed=discord_embed)
                self._last_update = datetime.utcnow()
            except discord.HTTPException as e:
                logger.warning(f"Failed to finish embed: {e}")
