"""
Discord progress reporter for CodeAgent human-in-the-loop support.

Provides:
- Live progress updates in Discord as the agent runs
- Human-in-the-loop via reply detection
- Users can reply to any agent message to provide feedback
- Notification controls: silent mode, pause/resume, on-demand status

Notification Modes:
- "all": Send all progress updates (default)
- "important": Only send important updates (approvals, errors, completion)
- "silent": No automatic updates, only respond to status requests
- "paused": Temporarily paused, can resume

Commands (reply to any agent message):
- "status" / "?" - Get current status
- "silent" / "quiet" / "shh" - Switch to silent mode
- "verbose" / "loud" / "updates" - Switch to all updates
- "pause" - Pause notifications temporarily
- "resume" - Resume notifications
- "stop" / "cancel" - Cancel the task
"""

import asyncio
import logging
from typing import Optional, Callable, Any, Literal
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import discord

logger = logging.getLogger(__name__)

# Notification mode type
NotificationMode = Literal["all", "important", "silent", "paused"]

# Phase emojis for visual feedback
PHASE_EMOJI = {
    "idle": "⏸️",
    "understanding": "🔍",
    "planning": "📋",
    "plan_review": "🤔",
    "coding": "💻",
    "testing": "🧪",
    "fixing": "🔧",
    "code_review": "📝",
    "committing": "📦",
    "complete": "✅",
    "failed": "❌",
}

# Phase descriptions
PHASE_DESCRIPTION = {
    "idle": "Idle",
    "understanding": "Analyzing codebase...",
    "planning": "Creating implementation plan...",
    "plan_review": "Reviewing plan...",
    "coding": "Implementing changes...",
    "testing": "Running tests...",
    "fixing": "Fixing errors...",
    "code_review": "Final code review...",
    "committing": "Committing changes...",
    "complete": "Complete!",
    "failed": "Failed",
}


class HumanFeedbackType(Enum):
    """Types of human feedback."""
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"
    CANCEL = "cancel"
    # Notification control commands
    STATUS = "status"
    SILENT = "silent"
    VERBOSE = "verbose"
    PAUSE = "pause"
    RESUME = "resume"


# Important phases that always get notifications (even in "important" mode)
IMPORTANT_PHASES = {"plan_review", "code_review", "complete", "failed", "committing"}


@dataclass
class HumanFeedback:
    """Feedback from human-in-the-loop."""
    type: HumanFeedbackType
    message: str
    user_id: int
    user_name: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProgressMessage:
    """A progress message sent to Discord."""
    message_id: int
    phase: str
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


class DiscordProgressReporter:
    """
    Reports CodeAgent progress to Discord with human-in-the-loop support.

    Usage:
        reporter = DiscordProgressReporter(channel, bot)

        # Start a task
        await reporter.start_task("Add dark mode", "owner/repo")

        # Update progress
        await reporter.update_phase("planning", "Analyzing 50 files...")

        # Wait for human feedback at plan review
        feedback = await reporter.request_approval(
            "plan_review",
            plan_content,
            timeout=300  # 5 min timeout
        )

        # Process feedback
        if feedback.type == HumanFeedbackType.APPROVE:
            continue_execution()
        elif feedback.type == HumanFeedbackType.MODIFY:
            incorporate_feedback(feedback.message)
    """

    def __init__(
        self,
        channel: discord.TextChannel,
        bot: discord.Client,
        user_id: int,
        user_name: str,
        notification_mode: NotificationMode = "all",
    ):
        self.channel = channel
        self.bot = bot
        self.user_id = user_id
        self.user_name = user_name

        # Task state
        self.task_id: Optional[str] = None
        self.task_description: str = ""
        self.repo: str = ""
        self.started_at: Optional[datetime] = None

        # Progress tracking
        self.current_phase: str = "idle"
        self.current_detail: str = ""  # Track current detail for status requests
        self.progress_messages: list[ProgressMessage] = []
        self.main_message: Optional[discord.Message] = None

        # Human-in-the-loop
        self._feedback_event: Optional[asyncio.Event] = None
        self._pending_feedback: Optional[HumanFeedback] = None
        self._listening_for_replies: bool = False
        self._message_ids: set[int] = set()  # Track our message IDs for reply detection

        # Notification control
        self._notification_mode: NotificationMode = notification_mode
        self._mode_before_pause: NotificationMode = "all"  # For pause/resume

    async def start_task(self, task: str, repo: str, task_id: str) -> discord.Message:
        """
        Start tracking a new task and send initial message.

        Returns the main progress message for reference.
        """
        self.task_id = task_id
        self.task_description = task
        self.repo = repo
        self.started_at = datetime.utcnow()
        self.current_phase = "understanding"
        self.progress_messages = []

        # Send initial message
        content = self._format_progress_message()
        self.main_message = await self.channel.send(content)
        self._message_ids.add(self.main_message.id)

        self.progress_messages.append(ProgressMessage(
            message_id=self.main_message.id,
            phase="understanding",
            content=content,
        ))

        # Start listening for replies
        self._start_reply_listener()

        return self.main_message

    async def update_phase(
        self,
        phase: str,
        detail: Optional[str] = None,
        update_main: bool = True,
    ) -> Optional[discord.Message]:
        """
        Update the current phase and send/edit progress message.

        Respects notification mode:
        - "all": Send all updates
        - "important": Only send for important phases (approvals, errors, completion)
        - "silent": Don't send automatic updates (user can still request status)
        - "paused": Same as silent but remembers previous mode

        Args:
            phase: New phase name
            detail: Optional detail message
            update_main: Whether to edit the main message or send a new one

        Returns:
            The sent/edited message (or None if suppressed)
        """
        self.current_phase = phase
        self.current_detail = detail or ""
        content = self._format_progress_message(detail)

        # Check if we should send this update based on notification mode
        if not self._should_send_notification(phase):
            logger.debug(f"Suppressing notification for phase {phase} (mode: {self._notification_mode})")
            return None

        if update_main and self.main_message:
            try:
                await self.main_message.edit(content=content)
                return self.main_message
            except discord.HTTPException:
                pass

        # Send new message for major phases
        if phase in ("planning", "coding", "testing", "code_review", "complete", "failed"):
            msg = await self.channel.send(content)
            self._message_ids.add(msg.id)
            self.progress_messages.append(ProgressMessage(
                message_id=msg.id,
                phase=phase,
                content=content,
            ))
            return msg

        return None

    def _should_send_notification(self, phase: str) -> bool:
        """Check if notification should be sent based on current mode."""
        if self._notification_mode == "all":
            return True
        elif self._notification_mode == "important":
            return phase in IMPORTANT_PHASES
        elif self._notification_mode in ("silent", "paused"):
            # Only send for approval requests (they need response)
            return phase in ("plan_review", "code_review")
        return True

    # ========== Notification Control Methods ==========

    def set_notification_mode(self, mode: NotificationMode) -> str:
        """
        Set the notification mode.

        Returns confirmation message.
        """
        old_mode = self._notification_mode
        self._notification_mode = mode

        mode_descriptions = {
            "all": "🔔 **Verbose mode** - You'll get all progress updates",
            "important": "🔕 **Important only** - Only approvals, errors, and completion",
            "silent": "🤫 **Silent mode** - No auto updates. Reply `status` or `?` anytime to check progress",
            "paused": "⏸️ **Paused** - Notifications paused. Reply `resume` to continue",
        }

        return mode_descriptions.get(mode, f"Mode set to: {mode}")

    def pause_notifications(self) -> str:
        """Pause notifications temporarily."""
        self._mode_before_pause = self._notification_mode
        self._notification_mode = "paused"
        return "⏸️ Notifications paused. Reply `resume` to continue, or `status` to check progress."

    def resume_notifications(self) -> str:
        """Resume notifications from pause."""
        if self._notification_mode == "paused":
            self._notification_mode = self._mode_before_pause
            return f"▶️ Notifications resumed ({self._notification_mode} mode)"
        return f"Already active ({self._notification_mode} mode)"

    def get_status_message(self) -> str:
        """Get current status as a formatted message."""
        emoji = PHASE_EMOJI.get(self.current_phase, "⏳")
        desc = PHASE_DESCRIPTION.get(self.current_phase, self.current_phase)

        elapsed = ""
        if self.started_at:
            duration = (datetime.utcnow() - self.started_at).total_seconds()
            mins, secs = divmod(int(duration), 60)
            elapsed = f" ({mins}m {secs}s)" if mins else f" ({secs}s)"

        mode_emoji = {
            "all": "🔔",
            "important": "🔕",
            "silent": "🤫",
            "paused": "⏸️",
        }.get(self._notification_mode, "")

        lines = [
            f"📊 **Status Update** `{self.task_id}`",
            f"",
            f"**Task:** {self.task_description[:100]}",
            f"**Repo:** {self.repo}",
            f"",
            f"{emoji} **Phase:** {desc}{elapsed}",
        ]

        if self.current_detail:
            lines.append(f"**Detail:** {self.current_detail[:200]}")

        lines.append(f"")
        lines.append(f"{mode_emoji} **Notification mode:** {self._notification_mode}")
        lines.append(f"")
        lines.append("_Reply: `silent`/`verbose`/`pause`/`resume`/`status`_")

        return "\n".join(lines)

    @property
    def notification_mode(self) -> NotificationMode:
        """Get current notification mode."""
        return self._notification_mode

    async def send_detail(self, text: str, force: bool = False) -> Optional[discord.Message]:
        """
        Send a detail message (for logs, errors, etc).

        Args:
            text: The message text
            force: If True, send even in silent mode

        Returns:
            The sent message, or None if suppressed
        """
        # In silent/paused mode, don't send detail messages unless forced
        if not force and self._notification_mode in ("silent", "paused"):
            return None

        msg = await self.channel.send(text)
        self._message_ids.add(msg.id)
        return msg

    async def request_approval(
        self,
        phase: str,
        content: str,
        timeout: float = 300.0,
        prompt: Optional[str] = None,
    ) -> HumanFeedback:
        """
        Request human approval for a phase.

        Sends the content and waits for user reply.

        Args:
            phase: Current phase (plan_review, code_review)
            content: Content to show (plan, code summary)
            timeout: Seconds to wait for response
            prompt: Custom prompt (defaults based on phase)

        Returns:
            HumanFeedback with user's response
        """
        self.current_phase = phase

        # Build approval message
        emoji = PHASE_EMOJI.get(phase, "📋")

        if prompt is None:
            if phase == "plan_review":
                prompt = "Reply **approve** to continue, **reject** to cancel, or describe changes you want."
            elif phase == "code_review":
                prompt = "Reply **approve** to commit, **reject** to discard, or describe issues to fix."
            else:
                prompt = "Reply to provide feedback."

        # Truncate content if too long
        if len(content) > 1500:
            content = content[:1500] + "\n\n*[truncated...]*"

        message_content = (
            f"{emoji} **{PHASE_DESCRIPTION.get(phase, phase)}**\n\n"
            f"{content}\n\n"
            f"---\n"
            f"💬 {prompt}"
        )

        # Send the approval request message
        approval_msg = await self.channel.send(message_content)
        self._message_ids.add(approval_msg.id)
        self.progress_messages.append(ProgressMessage(
            message_id=approval_msg.id,
            phase=phase,
            content=message_content,
        ))

        # Wait for feedback
        self._feedback_event = asyncio.Event()
        self._pending_feedback = None
        self._listening_for_replies = True

        try:
            await asyncio.wait_for(
                self._feedback_event.wait(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            # Auto-approve on timeout for plan review, reject for code review
            if phase == "plan_review":
                logger.info(f"Plan review timeout, auto-approving")
                return HumanFeedback(
                    type=HumanFeedbackType.APPROVE,
                    message="Auto-approved (timeout)",
                    user_id=0,
                    user_name="system",
                )
            else:
                logger.info(f"Code review timeout, auto-rejecting")
                return HumanFeedback(
                    type=HumanFeedbackType.REJECT,
                    message="Auto-rejected (timeout)",
                    user_id=0,
                    user_name="system",
                )
        finally:
            self._listening_for_replies = False

        return self._pending_feedback or HumanFeedback(
            type=HumanFeedbackType.APPROVE,
            message="",
            user_id=self.user_id,
            user_name=self.user_name,
        )

    async def handle_reply(self, message: discord.Message) -> bool:
        """
        Handle a reply to one of our messages.

        Called by the bot when a reply is detected.
        Uses AI to understand natural language intent.

        Returns:
            True if the reply was handled (was for us)
        """
        # Check if this is a reply to one of our messages
        if not message.reference or not message.reference.message_id:
            return False

        if message.reference.message_id not in self._message_ids:
            return False

        # Ignore our own messages
        if message.author.id == self.bot.user.id:
            return False

        content = message.content.strip()

        # Use AI to understand the intent
        intent = await self._parse_intent(content)

        # Handle based on intent
        if intent["type"] == "status":
            status = self.get_status_message()
            await message.reply(status)
            await message.add_reaction("📊")
            return True

        elif intent["type"] == "silent":
            response = self.set_notification_mode("silent")
            await message.reply(response)
            await message.add_reaction("🤫")
            return True

        elif intent["type"] == "verbose":
            response = self.set_notification_mode("all")
            await message.reply(response)
            await message.add_reaction("🔔")
            return True

        elif intent["type"] == "important":
            response = self.set_notification_mode("important")
            await message.reply(response)
            await message.add_reaction("🔕")
            return True

        elif intent["type"] == "pause":
            response = self.pause_notifications()
            await message.reply(response)
            await message.add_reaction("⏸️")
            return True

        elif intent["type"] == "resume":
            response = self.resume_notifications()
            await message.reply(response)
            await message.add_reaction("▶️")
            return True

        elif intent["type"] == "approve":
            feedback_type = HumanFeedbackType.APPROVE
        elif intent["type"] == "reject":
            feedback_type = HumanFeedbackType.REJECT
        elif intent["type"] == "cancel":
            feedback_type = HumanFeedbackType.CANCEL
        else:
            # Treat as modification/feedback
            feedback_type = HumanFeedbackType.MODIFY

        feedback = HumanFeedback(
            type=feedback_type,
            message=content,
            user_id=message.author.id,
            user_name=str(message.author),
        )

        # Store and signal
        self._pending_feedback = feedback
        if self._feedback_event:
            self._feedback_event.set()

        # Acknowledge
        emoji = "✅" if feedback_type == HumanFeedbackType.APPROVE else "📝" if feedback_type == HumanFeedbackType.MODIFY else "🛑"
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass

        return True

    async def _parse_intent(self, content: str) -> dict:
        """
        Use AI to parse natural language intent from user message.

        Returns dict with 'type' and optional 'detail'.
        """
        # Quick check for very short/obvious messages first (save API calls)
        content_lower = content.lower().strip()

        # Super obvious ones - don't need AI
        if content_lower in ("?", "y", "n", "ok", "no", "yes", "👍", "👎"):
            if content_lower in ("?",):
                return {"type": "status"}
            elif content_lower in ("y", "yes", "ok", "👍"):
                return {"type": "approve"}
            elif content_lower in ("n", "no", "👎"):
                return {"type": "reject"}

        # For anything else, use AI to understand intent
        try:
            from .models import model_router

            prompt = f"""Classify this user message into ONE intent. The user is replying to a coding agent that's working on a task.

Message: "{content}"

Current task phase: {self.current_phase}

Possible intents:
- status: User wants to know current progress (e.g., "what's happening", "how's it going", "where are you at", "?")
- silent: User wants to stop getting updates (e.g., "stop messaging me", "be quiet", "don't notify me", "shh", "stop updates")
- verbose: User wants more updates (e.g., "keep me updated", "tell me everything", "I want updates", "notify me")
- important: User wants only important updates (e.g., "only tell me important stuff", "just the key things")
- pause: User wants to temporarily pause notifications (e.g., "hold on", "brb", "pause for now")
- resume: User wants to resume notifications (e.g., "I'm back", "continue updating me")
- approve: User approves/agrees (e.g., "looks good", "go ahead", "approved", "ship it", "lgtm")
- reject: User rejects/disagrees (e.g., "no don't do that", "that's wrong", "reject")
- cancel: User wants to stop the whole task (e.g., "stop everything", "cancel the task", "abort", "kill it")
- feedback: User is giving specific feedback or instructions (anything else)

Reply with ONLY the intent word, nothing else."""

            response = await model_router.chat(
                model_id="claude",  # Fast model for quick classification
                messages=[{"role": "user", "content": prompt}],
                task_type="quick",
                temperature=0.1,
                max_tokens=20,
            )

            intent_text = response.get("content", "").strip().lower()

            # Map to valid intent
            valid_intents = ["status", "silent", "verbose", "important", "pause", "resume", "approve", "reject", "cancel", "feedback"]
            if intent_text in valid_intents:
                return {"type": intent_text}

            # Default to feedback if AI gives unexpected response
            return {"type": "feedback"}

        except Exception as e:
            logger.warning(f"Intent parsing failed, defaulting to feedback: {e}")
            # Fallback to simple keyword matching if AI fails
            return self._fallback_parse_intent(content_lower)

    def _fallback_parse_intent(self, content: str) -> dict:
        """Fallback keyword-based intent parsing if AI is unavailable."""
        content = content.lower()

        # Status
        if any(w in content for w in ["status", "progress", "what's", "whats", "how's", "hows", "where"]):
            return {"type": "status"}

        # Silent
        if any(w in content for w in ["stop", "quiet", "silent", "shut", "don't notify", "no updates", "stop update"]):
            return {"type": "silent"}

        # Verbose
        if any(w in content for w in ["update me", "keep me", "notify", "tell me", "want updates"]):
            return {"type": "verbose"}

        # Approve
        if any(w in content for w in ["approve", "lgtm", "looks good", "go ahead", "ship", "good"]):
            return {"type": "approve"}

        # Reject
        if any(w in content for w in ["reject", "don't", "wrong", "bad"]):
            return {"type": "reject"}

        # Cancel
        if any(w in content for w in ["cancel", "abort", "kill", "stop everything"]):
            return {"type": "cancel"}

        return {"type": "feedback"}

    async def complete(self, success: bool, summary: str):
        """
        Mark the task as complete and send final message.
        """
        self.current_phase = "complete" if success else "failed"
        self._stop_reply_listener()

        emoji = "✅" if success else "❌"
        elapsed = ""
        if self.started_at:
            duration = (datetime.utcnow() - self.started_at).total_seconds()
            elapsed = f" in {duration:.1f}s"

        content = (
            f"{emoji} **Task {'Completed' if success else 'Failed'}**{elapsed}\n\n"
            f"{summary}"
        )

        await self.channel.send(content)

    def _format_progress_message(self, detail: Optional[str] = None, show_controls: bool = False) -> str:
        """Format the main progress message."""
        emoji = PHASE_EMOJI.get(self.current_phase, "⏳")
        desc = PHASE_DESCRIPTION.get(self.current_phase, self.current_phase)

        elapsed = ""
        if self.started_at:
            duration = (datetime.utcnow() - self.started_at).total_seconds()
            elapsed = f" ({duration:.0f}s)"

        lines = [
            f"🤖 **Code Agent Task** `{self.task_id}`",
            f"",
            f"**Task:** {self.task_description[:100]}",
            f"**Repo:** {self.repo}",
            f"",
            f"{emoji} **Status:** {desc}{elapsed}",
        ]

        if detail:
            lines.append(f"")
            lines.append(detail)

        # Show hint on first message
        if show_controls or self.current_phase == "understanding":
            lines.append(f"")
            lines.append("---")
            lines.append("💡 _Reply anytime to give feedback or ask for status. Say \"stop updates\" if I'm too chatty!_")

        return "\n".join(lines)

    def _start_reply_listener(self):
        """Start listening for replies (registers with bot)."""
        self._listening_for_replies = True
        # The actual listener is implemented in bot.py
        # We just set the flag here

    def _stop_reply_listener(self):
        """Stop listening for replies."""
        self._listening_for_replies = False
        self._message_ids.clear()

    @property
    def is_listening(self) -> bool:
        """Check if we're listening for replies."""
        return self._listening_for_replies


# Registry of active progress reporters for reply routing
_active_reporters: dict[int, DiscordProgressReporter] = {}  # channel_id -> reporter


def register_reporter(channel_id: int, reporter: DiscordProgressReporter):
    """Register a progress reporter for reply routing."""
    _active_reporters[channel_id] = reporter


def unregister_reporter(channel_id: int):
    """Unregister a progress reporter."""
    _active_reporters.pop(channel_id, None)


def get_reporter(channel_id: int) -> Optional[DiscordProgressReporter]:
    """Get the active reporter for a channel."""
    return _active_reporters.get(channel_id)


async def route_reply(message: discord.Message) -> bool:
    """
    Route a reply message to the appropriate reporter.

    Called from bot.py when a message is received.

    Returns:
        True if the reply was handled by a reporter
    """
    channel_id = message.channel.id
    reporter = _active_reporters.get(channel_id)

    if reporter and reporter.is_listening:
        return await reporter.handle_reply(message)

    return False
