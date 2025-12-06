"""Discord bot - Full GitHub Issues bridge with native tool calling."""

import asyncio
import logging
from typing import Optional, Union

import discord
from discord.ext import commands, tasks

from .config import config
from .context import session_manager, ConversationSession
from .services.github import github_manager, TOOL_HANDLERS
from .services.github_graphql import github_graphql
from .services.github_pr import github_pr_manager
from .services.github_auth import init_github_app, github_app_auth
from .services.pollinations import pollinations_client
from .services.subscriptions import subscription_manager, init_notifier
from .services.code_agent.tools import TOOL_HANDLERS as CODE_AGENT_HANDLERS
from .services.webhook_server import start_webhook_server, stop_webhook_server

logger = logging.getLogger(__name__)

# =============================================================================
# PR MERGE NOTIFICATION (triggers embedding updates)
# =============================================================================
PR_MERGE_CHANNEL_ID = 1433858964658852081
PR_MERGE_WEBHOOK_ID = 1433141915397652532

# Supported image extensions
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

# Thread settings
THREAD_AUTO_ARCHIVE_MINUTES = 60
THREAD_HISTORY_LIMIT = 50


def is_admin(user: discord.User | discord.Member) -> bool:
    """Check if a user has any of the configured admin roles."""
    if not config.admin_role_ids:
        return False
    if isinstance(user, discord.Member):
        return any(role.id in config.admin_role_ids for role in user.roles)
    return False


def extract_image_urls(message: discord.Message) -> list[str]:
    """Extract image URLs from Discord message attachments and embeds."""
    image_urls = []
    for attachment in message.attachments:
        if any(attachment.filename.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
            image_urls.append(attachment.url)
    for embed in message.embeds:
        if embed.image and embed.image.url:
            image_urls.append(embed.image.url)
        if embed.thumbnail and embed.thumbnail.url:
            image_urls.append(embed.thumbnail.url)
    return image_urls


async def _code_search_handler(query: str, top_k: int = 10, **kwargs) -> dict:
    """Handler for code_search tool - semantic search across repository."""
    from .services.embeddings import search_code, get_stats

    # Validate top_k
    top_k = min(max(1, top_k), 10)

    try:
        results = await search_code(query, top_k=top_k)

        if not results:
            stats = get_stats()
            return {
                "results": [],
                "message": f"No matching code found. Embeddings contain {stats['total_chunks']} chunks."
            }

        return {
            "results": [
                {
                    "file": r["file_path"],
                    "lines": f"{r['start_line']}-{r['end_line']}",
                    "similarity": r["similarity"],
                    "code": r["content"][:2000]  # Truncate for response
                }
                for r in results
            ],
            "message": f"Found {len(results)} relevant code sections"
        }
    except Exception as e:
        logger.error(f"Code search failed: {e}")
        return {"error": str(e)}


async def fetch_thread_history(thread: discord.Thread, limit: int = THREAD_HISTORY_LIMIT) -> list[dict]:
    """
    Fetch message history from a thread and format for AI context.
    This is our "memory" - pulled fresh from Discord each time.
    """
    messages = []
    try:
        async for msg in thread.history(limit=limit, oldest_first=True):
            if msg.author.bot:
                messages.append({
                    "role": "assistant",
                    "content": msg.content
                })
            else:
                messages.append({
                    "role": "user",
                    "content": f"[{msg.author.name}]: {msg.content}"
                })
    except Exception as e:
        logger.warning(f"Failed to fetch thread history: {e}")
    return messages


class PollyBot(commands.Bot):
    """Discord bot for GitHub Issues bridge with tool calling."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.issue_notifier = None
        self.webhook_server = None

    async def setup_hook(self):
        """Called when the bot is starting up."""
        # Initialize GitHub App auth if configured
        if config.use_github_app:
            init_github_app(
                app_id=config.github_app_id,
                private_key=config.github_private_key,
                installation_id=config.github_installation_id
            )
            logger.info("GitHub App authentication initialized")
        else:
            logger.info("Using GitHub PAT authentication")

        # Register tool handlers with pollinations client
        for name, handler in TOOL_HANDLERS.items():
            pollinations_client.register_tool_handler(name, handler)
        logger.info(f"Registered {len(TOOL_HANDLERS)} GitHub tool handlers")

        # Register code agent tool handlers
        for name, handler in CODE_AGENT_HANDLERS.items():
            pollinations_client.register_tool_handler(name, handler)
        logger.info(f"Registered {len(CODE_AGENT_HANDLERS)} code agent tool handlers")

        # Register code_search handler if embeddings enabled
        if config.local_embeddings_enabled:
            from .services.embeddings import search_code
            pollinations_client.register_tool_handler("code_search", _code_search_handler)
            logger.info("Registered code_search tool handler (embeddings enabled)")

        # Register web_search handler (always available)
        from .services.pollinations import web_search_handler
        pollinations_client.register_tool_handler("web_search", web_search_handler)
        logger.info("Registered web_search tool handler")

        # Initialize and start the issue notifier
        self.issue_notifier = init_notifier(self)
        await self.issue_notifier.start()
        logger.info("Issue notification system started")

        # Start GitHub webhook server for bidirectional communication
        self.webhook_server = await start_webhook_server(self)
        logger.info("GitHub webhook server started")

        self.cleanup_sessions.start()
        logger.info("Bot setup complete")

    async def close(self):
        """Clean up resources when bot shuts down."""
        self.cleanup_sessions.cancel()
        if self.issue_notifier:
            await self.issue_notifier.stop()
        if self.webhook_server:
            await stop_webhook_server()
        await pollinations_client.close()
        await github_manager.close()
        await github_graphql.close()
        await github_pr_manager.close()
        if github_app_auth:
            await github_app_auth.close()
        # Clean up code agent
        from .services.code_agent import code_agent
        await code_agent.close()
        # Clean up embeddings if enabled
        if config.local_embeddings_enabled:
            from .services.embeddings import close as close_embeddings
            await close_embeddings()
        await super().close()

    @tasks.loop(minutes=1)
    async def cleanup_sessions(self):
        """Periodically clean up expired sessions."""
        cleaned = session_manager.cleanup_expired()
        if cleaned > 0:
            logger.debug(f"Cleaned {cleaned} expired sessions")

    @cleanup_sessions.before_loop
    async def before_cleanup(self):
        """Wait until the bot is ready before starting cleanup task."""
        await self.wait_until_ready()


bot = PollyBot()


@bot.event
async def on_ready():
    """Called when the bot is ready."""
    logger.info(f"{bot.user} is now online!")
    logger.info(f"Connected to {len(bot.guilds)} guild(s)")

    # Initialize embeddings if enabled (runs in background)
    if config.local_embeddings_enabled:
        from .services.embeddings import initialize as init_embeddings
        asyncio.create_task(init_embeddings())
        logger.info("Local embeddings initialization started")


async def _check_reply_to_bot(message: discord.Message) -> tuple[bool, discord.Message | None]:
    """
    Check if message is a reply to bot. Returns (is_reply_to_bot, referenced_message).
    Caches the fetched message to avoid duplicate fetches.
    """
    if not message.reference or not message.reference.message_id:
        return False, None

    # Try cached reference first (Discord caches recent messages)
    if message.reference.cached_message:
        ref_msg = message.reference.cached_message
        return ref_msg.author == bot.user, ref_msg

    # Fetch if not cached
    try:
        ref_msg = await message.channel.fetch_message(message.reference.message_id)
        return ref_msg.author == bot.user, ref_msg
    except Exception:
        return False, None


@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages."""
    if message.author == bot.user:
        return

    # PR merge notification - triggers embedding update
    # Use webhook_id for reliable webhook detection (author.id also works but this is cleaner)
    if (
        config.local_embeddings_enabled and
        message.channel.id == PR_MERGE_CHANNEL_ID and
        message.webhook_id == PR_MERGE_WEBHOOK_ID
    ):
        from .services.embeddings import schedule_update
        await schedule_update()
        logger.info("PR merge detected - embedding update scheduled")
        return

    # Check for code agent human-in-the-loop replies first
    # This allows users to reply to agent progress messages for feedback
    if message.reference and message.reference.message_id:
        from .services.code_agent.discord_progress import route_reply
        try:
            handled = await route_reply(message)
            if handled:
                # Reply was for code agent, don't process further
                return
        except Exception as e:
            logger.warning(f"Error routing code agent reply: {e}")

    # Handle DMs - only subscription commands allowed
    if isinstance(message.channel, discord.DMChannel):
        await handle_dm_message(message)
        return

    # Check reply status ONCE (reuse result throughout)
    is_reply_to_bot, ref_msg = await _check_reply_to_bot(message)

    # Check if in a thread
    if isinstance(message.channel, discord.Thread):
        session = session_manager.get_session(message.channel.id)

        # ONLY respond if: @mentioned OR replying to bot's message
        # Having a session is NOT enough - user must explicitly engage
        should_respond = (
            (bot.user.mentioned_in(message) and not message.mention_everyone) or
            is_reply_to_bot
        )

        if not should_respond:
            # In thread but not mentioned and not replying to bot - ignore
            return

        # Extract text
        text = message.content
        for mention in message.mentions:
            text = text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        text = text.strip()

        # Handle reply context in threads too
        if message.reference and message.reference.message_id:
            text = await handle_reply_context(message, text, ref_msg)

        image_urls = extract_image_urls(message)

        # If no text but replying or has images, let AI handle it
        if not text and not image_urls:
            text = "[User mentioned bot without text - greet them or ask how you can help]"
        if not text and image_urls:
            text = "[User attached screenshot(s)]"

        # Create session if needed (handles bot restart scenario)
        if not session:
            topic = pollinations_client.get_topic_summary_fast(text)
            session = session_manager.create_session(
                channel_id=message.channel.parent_id or message.channel.id,
                thread_id=message.channel.id,
                user_id=message.author.id,
                user_name=str(message.author),
                initial_message=text,
                topic_summary=topic,
                image_urls=image_urls
            )

        await handle_thread_message(message, session)
        return

    # Respond if @mentioned OR if replying to bot's message
    if not bot.user.mentioned_in(message) and not is_reply_to_bot:
        return

    if message.mention_everyone:
        return

    # Extract message text
    text = message.content
    for mention in message.mentions:
        text = text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
    text = text.strip()

    # Handle reply context (get context from referenced message)
    # Pass ref_msg if already fetched to avoid duplicate network call
    if message.reference and message.reference.message_id:
        text = await handle_reply_context(message, text, ref_msg)

    image_urls = extract_image_urls(message)

    # If no text but replying or has images, let AI handle it
    if not text and not image_urls:
        text = "[User mentioned bot without text - greet them or ask how you can help]"
    if not text and image_urls:
        text = "[User attached screenshot(s)]"

    # Check if message already has a thread - if so, respond there instead of creating new
    if hasattr(message, 'thread') and message.thread:
        # Message already has a thread, use it
        thread = message.thread
        topic = pollinations_client.get_topic_summary_fast(text)
        session = session_manager.get_session(thread.id)
        if not session:
            session = session_manager.create_session(
                channel_id=message.channel.id,
                thread_id=thread.id,
                user_id=message.author.id,
                user_name=str(message.author),
                initial_message=text,
                topic_summary=topic,
                image_urls=image_urls
            )
        async with thread.typing():
            await process_message(
                channel=thread,
                user=message.author,
                text=text,
                image_urls=image_urls,
                session=session,
                reply_to=None
            )
        return

    # Create thread and start new conversation
    await start_conversation(message, text, image_urls)


async def handle_dm_message(message: discord.Message):
    """
    Handle DM messages - only subscription commands are allowed.

    Supported commands:
    - subscribe #123 or subscribe to 123
    - unsubscribe #123 or unsubscribe from 123
    - unsubscribe all
    - list subscriptions / my subscriptions
    """
    import re
    from .services.github import TOOL_HANDLERS

    text = message.content.strip().lower()
    user_id = message.author.id

    async with message.channel.typing():
        # Subscribe command
        subscribe_match = re.search(r'subscribe\s+(?:to\s+)?#?(\d+)', text)
        if subscribe_match and 'unsubscribe' not in text:
            issue_number = int(subscribe_match.group(1))
            result = await TOOL_HANDLERS["subscribe_issue"](
                issue_number=issue_number,
                user_id=user_id,
                channel_id=message.channel.id,
                guild_id=None  # DM has no guild
            )
            await message.reply(result.get("message", "Done!"))
            return

        # Unsubscribe all command
        if 'unsubscribe' in text and 'all' in text:
            result = await TOOL_HANDLERS["unsubscribe_all"](user_id=user_id)
            await message.reply(result.get("message", "Done!"))
            return

        # Unsubscribe from specific issue
        unsubscribe_match = re.search(r'unsubscribe\s+(?:from\s+)?#?(\d+)', text)
        if unsubscribe_match:
            issue_number = int(unsubscribe_match.group(1))
            result = await TOOL_HANDLERS["unsubscribe_issue"](
                issue_number=issue_number,
                user_id=user_id
            )
            await message.reply(result.get("message", "Done!"))
            return

        # List subscriptions
        if 'subscriptions' in text or 'list' in text or 'my sub' in text:
            result = await TOOL_HANDLERS["list_subscriptions"](user_id=user_id)
            await message.reply(result.get("message", "No subscriptions found."))
            return

        # Unknown command - show help
        help_text = (
            "**DM Commands:**\n"
            "• `subscribe #123` - Subscribe to issue updates\n"
            "• `unsubscribe #123` - Unsubscribe from an issue\n"
            "• `unsubscribe all` - Unsubscribe from all issues\n"
            "• `list subscriptions` - See your subscriptions\n\n"
            "For other requests, please @mention me in a server channel!"
        )
        await message.reply(help_text)


async def handle_reply_context(message: discord.Message, text: str, ref_msg: discord.Message = None) -> str:
    """Handle when message is a reply to another message. Uses cached ref_msg if provided."""
    try:
        # Use provided ref_msg to avoid duplicate fetch
        if ref_msg is None:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
        if text and ref_msg.content:
            return f"{ref_msg.content}\n\nAdditional context: {text}"
        elif not text:
            return ref_msg.content
    except Exception as e:
        logger.warning(f"Failed to fetch referenced message: {e}")
    return text


async def start_conversation(message: discord.Message, text: str, image_urls: list[str]):
    """Start a new conversation in a thread."""
    # Quick topic extraction for thread name
    topic = pollinations_client.get_topic_summary_fast(text)
    thread_name = f"Issue: {topic}"[:100]

    try:
        thread = await message.create_thread(
            name=thread_name,
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES
        )
    except discord.Forbidden:
        await message.reply("I don't have permission to create threads. Please grant me 'Create Public Threads' permission.")
        return
    except discord.HTTPException as e:
        logger.error(f"Failed to create thread: {e}")
        await message.reply("Couldn't create a thread. Please try again.")
        return

    # Create session
    session = session_manager.create_session(
        channel_id=message.channel.id,
        thread_id=thread.id,
        user_id=message.author.id,
        user_name=str(message.author),
        initial_message=text,
        topic_summary=topic,
        image_urls=image_urls
    )

    # Process the message with tool calling
    async with thread.typing():
        await process_message(
            channel=thread,
            user=message.author,
            text=text,
            image_urls=image_urls,
            session=session
        )


async def handle_thread_message(message: discord.Message, session: ConversationSession):
    """Handle a message in an existing thread."""
    image_urls = extract_image_urls(message)

    # Add to session
    session_manager.add_to_session(
        session=session,
        role="user",
        content=message.content,
        author=str(message.author),
        author_id=message.author.id,
        image_urls=image_urls
    )

    async with message.channel.typing():
        # Fetch thread history for context
        thread_history = await fetch_thread_history(message.channel)

        await process_message(
            channel=message.channel,
            user=message.author,
            text=message.content,
            image_urls=image_urls,
            session=session,
            thread_history=thread_history,
            reply_to=message  # Reply to user's message so they get pinged
        )


async def process_message(
    channel: Union[discord.Thread, discord.TextChannel],
    user: Union[discord.User, discord.Member],
    text: str,
    image_urls: list[str],
    session: ConversationSession,
    thread_history: Optional[list[dict]] = None,
    reply_to: Optional[discord.Message] = None
):
    """
    Process a message using native tool calling.

    The AI will:
    1. Analyze the user's request
    2. Call appropriate tools (search, get_issue, create, etc.)
    3. Receive tool results
    4. Format a nice response

    All tool calls happen in parallel when possible.
    """
    # Check if user is admin (has admin role)
    user_is_admin = is_admin(user)

    # Store original handlers
    original_handlers = {
        "github_issue": TOOL_HANDLERS["github_issue"],
        "github_project": TOOL_HANDLERS["github_project"],
        "github_pr": TOOL_HANDLERS["github_pr"],
        "github_code": CODE_AGENT_HANDLERS.get("github_code"),
    }

    # Tool-specific admin actions (some actions like "create" are admin for PRs but not issues)
    ISSUE_ADMIN_ACTIONS = {"close", "reopen", "edit", "label", "unlabel", "assign", "unassign",
                           "milestone", "lock", "link", "create_sub_issue", "add_sub_issue", "remove_sub_issue"}
    PR_ADMIN_ACTIONS = {"request_review", "remove_reviewer", "approve", "request_changes", "merge",
                        "update", "create", "convert_to_draft", "ready_for_review", "update_branch",
                        "inline_comment", "suggest", "resolve_thread", "unresolve_thread",
                        "enable_auto_merge", "disable_auto_merge", "close", "reopen"}
    PROJECT_ADMIN_ACTIONS = {"add", "remove", "set_status", "set_field"}

    def check_admin(tool_name: str, action: str) -> dict | None:
        """
        Global admin check. Returns error dict if blocked, None if allowed.
        - github_code: ALL actions require admin (tool modifies repos)
        - Others: Check tool-specific admin actions
        """
        if tool_name == "github_code":
            # Code agent is entirely admin-only
            if not user_is_admin:
                return {"error": "Code agent requires admin permissions. This tool can modify repository code, create branches, and open PRs - ask a team member with admin access!"}
        elif not user_is_admin:
            action_lower = action.lower()
            # Check tool-specific admin actions
            if tool_name == "github_issue" and action_lower in ISSUE_ADMIN_ACTIONS:
                return {"error": f"The '{action}' action requires admin permissions. Ask a team member with admin access!"}
            elif tool_name == "github_pr" and action_lower in PR_ADMIN_ACTIONS:
                return {"error": f"The '{action}' action requires admin permissions. Ask a team member with admin access!"}
            elif tool_name == "github_project" and action_lower in PROJECT_ADMIN_ACTIONS:
                return {"error": f"The '{action}' action requires admin permissions. Ask a team member with admin access!"}
        return None

    async def wrapped_github_issue(**kwargs):
        """Wrapper that injects context and checks admin permissions."""
        if err := check_admin("github_issue", kwargs.get("action", "")):
            return err
        kwargs["reporter"] = session.original_author_name
        kwargs["user_id"] = user.id
        kwargs["channel_id"] = channel.id
        kwargs["guild_id"] = channel.guild.id if hasattr(channel, 'guild') and channel.guild else None
        return await original_handlers["github_issue"](**kwargs)

    async def wrapped_github_project(**kwargs):
        """Wrapper that checks admin permissions for project write actions."""
        if err := check_admin("github_project", kwargs.get("action", "")):
            return err
        return await original_handlers["github_project"](**kwargs)

    async def wrapped_github_pr(**kwargs):
        """Wrapper that injects context and checks admin permissions for PR actions."""
        if err := check_admin("github_pr", kwargs.get("action", "")):
            return err
        kwargs["reporter"] = session.original_author_name
        return await original_handlers["github_pr"](**kwargs)

    async def wrapped_github_code(**kwargs):
        """Wrapper that injects Discord context for code agent (admin only)."""
        if err := check_admin("github_code", kwargs.get("action", "")):
            return err
        if original_handlers["github_code"] is None:
            return {"error": "Code agent not available"}
        # Inject Discord context
        kwargs["discord_channel"] = channel
        kwargs["discord_bot"] = bot
        kwargs["discord_user_id"] = user.id
        kwargs["discord_user_name"] = str(user)
        kwargs["_is_admin"] = True  # Already checked above
        kwargs.setdefault("interactive", True)
        kwargs.setdefault("human_review", True)
        return await original_handlers["github_code"](**kwargs)

    # Register wrapped handlers temporarily
    pollinations_client.register_tool_handler("github_issue", wrapped_github_issue)
    pollinations_client.register_tool_handler("github_project", wrapped_github_project)
    pollinations_client.register_tool_handler("github_pr", wrapped_github_pr)
    if original_handlers["github_code"]:
        pollinations_client.register_tool_handler("github_code", wrapped_github_code)

    try:
        # Process with native tool calling
        result = await pollinations_client.process_with_tools(
            user_message=text,
            discord_username=str(user),
            thread_history=thread_history,
            image_urls=image_urls,
            is_admin=user_is_admin
        )

        response_text = result.get("response", "")
        tool_calls = result.get("tool_calls", [])
        tool_results = result.get("tool_results", [])

        # Log tool usage for debugging
        if tool_calls:
            tool_names = [tc["function"]["name"] for tc in tool_calls]
            logger.info(f"Tools called: {', '.join(tool_names)}")

        # Check if issue was created or comment added
        for tool_result in tool_results:
            if isinstance(tool_result, dict):
                # Handle successful issue creation
                if tool_result.get("success") and tool_result.get("issue_url"):
                    issue_url = tool_result["issue_url"]
                    issue_number = tool_result.get("issue_number")

                    if issue_number:
                        link = f"[Issue #{issue_number}](<{issue_url}>)"
                    else:
                        link = f"[Issue](<{issue_url}>)"

                    # Add link to response if not already there
                    if issue_url not in response_text:
                        response_text += f"\n\n{link}"

                    # Clear session after successful creation
                    session_manager.clear_session(session)

                    # Archive thread after issue creation
                    if response_text:
                        await send_long_message(channel, response_text, reply_to=reply_to)
                    await archive_thread(channel)
                    return

        # Send response
        if response_text:
            await send_long_message(channel, response_text, reply_to=reply_to)
        else:
            fallback_msg = "I processed your request but have nothing to say. Try asking differently?"
            if reply_to:
                await reply_to.reply(fallback_msg)
            else:
                await channel.send(fallback_msg)

    finally:
        # Restore original handlers
        pollinations_client.register_tool_handler("github_issue", original_handlers["github_issue"])
        pollinations_client.register_tool_handler("github_project", original_handlers["github_project"])
        pollinations_client.register_tool_handler("github_pr", original_handlers["github_pr"])
        if original_handlers["github_code"]:
            pollinations_client.register_tool_handler("github_code", original_handlers["github_code"])


async def send_long_message(
    channel: discord.TextChannel,
    text: str,
    max_length: int = 2000,
    reply_to: Optional[discord.Message] = None
):
    """Send a message, splitting if too long. First chunk replies to message if provided."""
    if len(text) <= max_length:
        if reply_to:
            await reply_to.reply(text)
        else:
            await channel.send(text)
        return

    # Split on newlines first, then by length
    chunks = []
    current_chunk = ""

    for line in text.split("\n"):
        if len(current_chunk) + len(line) + 1 <= max_length:
            current_chunk += line + "\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = line + "\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    for i, chunk in enumerate(chunks):
        if chunk:
            # Reply to user's message for first chunk only
            if i == 0 and reply_to:
                await reply_to.reply(chunk)
            else:
                await channel.send(chunk)


async def archive_thread(channel: Union[discord.Thread, discord.TextChannel]):
    """Archive thread if applicable."""
    if isinstance(channel, discord.Thread):
        try:
            await channel.edit(archived=True)
        except discord.HTTPException:
            pass
