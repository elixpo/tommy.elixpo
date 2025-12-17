"""
Discord Search Service - Full guild search capabilities via HTTP API.

Provides unrestricted search access to:
- Messages (via preview API)
- Members (by name, nickname, role)
- Channels (text, voice, forum, categories)
- Threads (active and archived)
- Roles (find roles, list members with role)
"""

import aiohttp
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import discord

from ..config import config

logger = logging.getLogger(__name__)

# Discord API base URL
DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordSearchClient:
    """HTTP client for Discord search API."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def headers(self) -> dict:
        """Get authorization headers."""
        return {
            "Authorization": f"Bot {config.discord_token}",
            "Content-Type": "application/json",
        }

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close the session."""
        if self._session and not self._session.closed:
            await self._session.close()

    # =========================================================================
    # MESSAGE SEARCH (Preview API)
    # =========================================================================

    async def search_messages(
        self,
        guild_id: int,
        query: str,
        channel_id: Optional[int] = None,
        author_id: Optional[int] = None,
        mentions: Optional[int] = None,
        has: Optional[str] = None,  # link, embed, file, video, image, sound, sticker
        before: Optional[str] = None,  # snowflake or date
        after: Optional[str] = None,  # snowflake or date
        limit: int = 25,
        offset: int = 0,
        accessible_channel_ids: Optional[set] = None,  # SECURITY: Filter to user's accessible channels
    ) -> Dict[str, Any]:
        """
        Search messages in a guild using Discord's preview search API.

        Args:
            guild_id: The guild to search in
            query: Search query text
            channel_id: Filter to specific channel
            author_id: Filter by message author
            mentions: Filter messages mentioning this user
            has: Filter by attachment type (link, embed, file, video, image, sound, sticker)
            before: Messages before this date/snowflake
            after: Messages after this date/snowflake
            limit: Max results (default 25, max 25)
            offset: Offset for pagination
            accessible_channel_ids: SECURITY - only return messages from these channels

        Returns:
            Search results with messages and metadata
        """
        session = await self.get_session()

        # Build query params
        params = {"content": query}

        if channel_id:
            params["channel_id"] = str(channel_id)
        if author_id:
            params["author_id"] = str(author_id)
        if mentions:
            params["mentions"] = str(mentions)
        if has:
            params["has"] = has
        if before:
            params["max_id"] = before
        if after:
            params["min_id"] = after
        if limit:
            params["limit"] = min(limit, 25)
        if offset:
            params["offset"] = offset

        url = f"{DISCORD_API_BASE}/guilds/{guild_id}/messages/search"

        try:
            async with session.get(url, headers=self.headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    messages = self._format_messages(data.get("messages", []))

                    # SECURITY: Filter messages to only channels user can access
                    if accessible_channel_ids is not None:
                        original_count = len(messages)
                        messages = [
                            m for m in messages
                            if int(m.get("channel_id", 0)) in accessible_channel_ids
                        ]
                        filtered_count = original_count - len(messages)
                        if filtered_count > 0:
                            logger.info(f"SECURITY: Filtered {filtered_count} messages from private channels")

                    return {
                        "success": True,
                        "total_results": len(messages),  # Adjusted count after filtering
                        "messages": messages,
                    }
                elif resp.status == 403:
                    return {"error": "Bot doesn't have permission to search messages in this guild"}
                elif resp.status == 429:
                    retry_after = resp.headers.get("Retry-After", "unknown")
                    return {"error": f"Rate limited. Retry after {retry_after} seconds"}
                else:
                    text = await resp.text()
                    logger.error(f"Message search failed: {resp.status} - {text}")
                    return {"error": f"Search failed: {resp.status}"}
        except Exception as e:
            logger.error(f"Message search error: {e}")
            return {"error": str(e)}

    def _format_messages(self, messages: List[List[Dict]]) -> List[Dict]:
        """Format message results for readability."""
        formatted = []
        for msg_group in messages:
            for msg in msg_group:
                formatted.append({
                    "id": msg.get("id"),
                    "content": msg.get("content", "")[:500],  # Truncate long messages
                    "author": msg.get("author", {}).get("username", "Unknown"),
                    "author_id": msg.get("author", {}).get("id"),
                    "channel_id": msg.get("channel_id"),
                    "timestamp": msg.get("timestamp"),
                    "attachments": len(msg.get("attachments", [])),
                    "embeds": len(msg.get("embeds", [])),
                    "jump_url": f"https://discord.com/channels/{msg.get('guild_id', '@me')}/{msg.get('channel_id')}/{msg.get('id')}",
                })
        return formatted

    # =========================================================================
    # MEMBER SEARCH
    # =========================================================================

    async def search_members(
        self,
        guild: discord.Guild,
        query: Optional[str] = None,
        user_id: Optional[int] = None,
        role_id: Optional[int] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Search members in a guild.

        Args:
            guild: The Discord guild object
            query: Search by name/nickname (partial match)
            user_id: Look up specific member by ID
            role_id: Filter by role
            limit: Max results

        Returns:
            List of matching members
        """
        try:
            members = []

            if user_id:
                # Direct lookup by user ID
                member = guild.get_member(user_id)
                if not member:
                    # Try fetching from API if not in cache
                    try:
                        member = await guild.fetch_member(user_id)
                    except discord.NotFound:
                        return {"success": True, "count": 0, "members": [], "note": f"User {user_id} not found in this server"}
                members = [member]
            elif query:
                # Use guild.query_members for name search
                found = await guild.query_members(query=query, limit=limit)
                members = found
            elif role_id:
                # Get role and its members
                role = guild.get_role(role_id)
                if role:
                    members = role.members[:limit]
                else:
                    return {"error": f"Role {role_id} not found"}
            else:
                # Return first N members
                members = list(guild.members)[:limit]

            formatted = []
            for m in members:
                formatted.append({
                    "id": str(m.id),
                    "username": m.name,
                    "display_name": m.display_name,
                    "nickname": m.nick,
                    "roles": [r.name for r in m.roles if r.name != "@everyone"],
                    "joined_at": m.joined_at.isoformat() if m.joined_at else None,
                    "is_bot": m.bot,
                })

            return {
                "success": True,
                "count": len(formatted),
                "members": formatted,
            }
        except Exception as e:
            logger.error(f"Member search error: {e}")
            return {"error": str(e)}

    # =========================================================================
    # CHANNEL SEARCH
    # =========================================================================

    async def search_channels(
        self,
        guild: discord.Guild,
        query: Optional[str] = None,
        channel_type: Optional[str] = None,  # text, voice, forum, category, thread
        limit: int = 50,
        can_view_channel: callable = None,  # SECURITY: Filter by user permissions
    ) -> Dict[str, Any]:
        """
        Search channels in a guild.

        Args:
            guild: The Discord guild object
            query: Search by channel name (partial match)
            channel_type: Filter by type (text, voice, forum, category, thread)
            limit: Max results
            can_view_channel: SECURITY - function to check if user can view channel

        Returns:
            List of matching channels
        """
        try:
            type_map = {
                "text": discord.ChannelType.text,
                "voice": discord.ChannelType.voice,
                "forum": discord.ChannelType.forum,
                "category": discord.ChannelType.category,
                "news": discord.ChannelType.news,
                "stage": discord.ChannelType.stage_voice,
            }

            channels = list(guild.channels)

            # SECURITY: Filter to only channels user can view
            if can_view_channel:
                channels = [c for c in channels if can_view_channel(c)]

            # Filter by type
            if channel_type and channel_type.lower() in type_map:
                target_type = type_map[channel_type.lower()]
                channels = [c for c in channels if c.type == target_type]

            # Filter by query
            if query:
                query_lower = query.lower()
                channels = [c for c in channels if query_lower in c.name.lower()]

            # Limit results
            channels = channels[:limit]

            formatted = []
            for c in channels:
                formatted.append({
                    "id": str(c.id),
                    "name": c.name,
                    "type": str(c.type).split(".")[-1],
                    "category": c.category.name if c.category else None,
                    "position": c.position,
                    "mention": c.mention,
                })

            return {
                "success": True,
                "count": len(formatted),
                "channels": formatted,
            }
        except Exception as e:
            logger.error(f"Channel search error: {e}")
            return {"error": str(e)}

    # =========================================================================
    # THREAD SEARCH
    # =========================================================================

    async def search_threads(
        self,
        guild: discord.Guild,
        query: Optional[str] = None,
        include_archived: bool = True,
        limit: int = 50,
        can_view_channel: callable = None,  # SECURITY: Filter by user permissions
    ) -> Dict[str, Any]:
        """
        Search threads in a guild.

        Args:
            guild: The Discord guild object
            query: Search by thread name (partial match)
            include_archived: Include archived threads
            limit: Max results
            can_view_channel: SECURITY - function to check if user can view channel

        Returns:
            List of matching threads
        """
        try:
            threads = []

            # Get active threads (only from accessible channels)
            active_threads = guild.threads
            for t in active_threads:
                # SECURITY: Only include threads from channels user can access
                if can_view_channel and t.parent:
                    if not can_view_channel(t.parent):
                        continue
                threads.append(t)

            # Get archived threads from each text channel (only accessible ones)
            if include_archived:
                for channel in guild.text_channels:
                    # SECURITY: Skip channels user can't access
                    if can_view_channel and not can_view_channel(channel):
                        continue
                    try:
                        async for thread in channel.archived_threads(limit=50):
                            threads.append(thread)
                    except discord.Forbidden:
                        continue
                    except Exception:
                        continue

            # Filter by query
            if query:
                query_lower = query.lower()
                threads = [t for t in threads if query_lower in t.name.lower()]

            # Limit results
            threads = threads[:limit]

            formatted = []
            for t in threads:
                formatted.append({
                    "id": str(t.id),
                    "name": t.name,
                    "parent_channel": t.parent.name if t.parent else None,
                    "owner_id": str(t.owner_id) if t.owner_id else None,
                    "archived": t.archived,
                    "locked": t.locked,
                    "message_count": t.message_count,
                    "member_count": t.member_count,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                    "jump_url": t.jump_url,
                })

            return {
                "success": True,
                "count": len(formatted),
                "threads": formatted,
            }
        except Exception as e:
            logger.error(f"Thread search error: {e}")
            return {"error": str(e)}

    # =========================================================================
    # ROLE SEARCH
    # =========================================================================

    async def search_roles(
        self,
        guild: discord.Guild,
        query: Optional[str] = None,
        include_members: bool = False,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Search roles in a guild.

        Args:
            guild: The Discord guild object
            query: Search by role name (partial match)
            include_members: Include member list for each role
            limit: Max results

        Returns:
            List of matching roles
        """
        try:
            roles = list(guild.roles)

            # Filter by query
            if query:
                query_lower = query.lower()
                roles = [r for r in roles if query_lower in r.name.lower()]

            # Remove @everyone
            roles = [r for r in roles if r.name != "@everyone"]

            # Limit results
            roles = roles[:limit]

            formatted = []
            for r in roles:
                role_data = {
                    "id": str(r.id),
                    "name": r.name,
                    "color": str(r.color),
                    "position": r.position,
                    "mentionable": r.mentionable,
                    "member_count": len(r.members),
                    "mention": r.mention,
                }
                if include_members:
                    role_data["members"] = [
                        {"id": str(m.id), "name": m.display_name}
                        for m in r.members[:20]  # Limit members per role
                    ]
                formatted.append(role_data)

            return {
                "success": True,
                "count": len(formatted),
                "roles": formatted,
            }
        except Exception as e:
            logger.error(f"Role search error: {e}")
            return {"error": str(e)}


# Singleton instance
discord_search_client = DiscordSearchClient()


# =============================================================================
# TOOL HANDLER
# =============================================================================

async def tool_discord_search(
    action: str,
    query: Optional[str] = None,
    channel_id: Optional[int] = None,
    channel_name: Optional[str] = None,
    user_id: Optional[int] = None,
    role_id: Optional[int] = None,
    role_name: Optional[str] = None,
    channel_type: Optional[str] = None,
    include_archived: bool = True,
    include_members: bool = False,
    has: Optional[str] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    limit: int = 25,
    # Context injected by pollinations client
    _context: dict = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Search EVERYTHING in the Discord server.

    Actions:
        - messages: Search message content (query required)
        - members: Search members by name/nickname or role
        - channels: Search channels by name or type
        - threads: Search threads by name
        - roles: Search roles by name

    Args:
        action: What to search (messages, members, channels, threads, roles)
        query: Search term (required for messages)
        channel_id: Filter messages to specific channel
        channel_name: Find channel by name (alternative to channel_id)
        user_id: Look up member by ID, or filter messages by author
        role_id: Filter members by role
        role_name: Find role by name (alternative to role_id)
        channel_type: Filter channels by type (text, voice, forum, category)
        include_archived: Include archived threads (default True)
        include_members: Include member list for roles (default False)
        has: Filter messages by attachment type (link, embed, file, video, image)
        before: Messages before this date/snowflake
        after: Messages after this date/snowflake
        limit: Max results (default 25)

    Returns:
        Search results based on action type
    """
    # Get guild from context
    if not _context:
        return {"error": "No context provided - cannot access Discord guild"}

    guild = _context.get("discord_guild")
    if not guild:
        return {"error": "Discord guild not available in context"}

    # SECURITY: Get the requesting user to filter results by their permissions
    requesting_user_id = _context.get("user_id")
    requesting_member = guild.get_member(requesting_user_id) if requesting_user_id else None

    # Helper to check if user can view a channel
    def can_view_channel(channel) -> bool:
        """Check if the requesting user has permission to view a channel."""
        if not requesting_member:
            return False  # No user context = deny access
        # Check view_channel permission
        perms = channel.permissions_for(requesting_member)
        return perms.view_channel

    # Get list of channel IDs user can access (for message search filtering)
    accessible_channel_ids = set()
    if requesting_member:
        for ch in guild.channels:
            if hasattr(ch, 'permissions_for'):
                if ch.permissions_for(requesting_member).view_channel:
                    accessible_channel_ids.add(ch.id)

    action = action.lower()

    # Resolve channel_name to channel_id if provided (only if user can access it)
    if channel_name and not channel_id:
        for ch in guild.channels:
            if channel_name.lower() in ch.name.lower():
                if can_view_channel(ch):
                    channel_id = ch.id
                    break
                else:
                    return {"error": f"You don't have permission to access channel '{ch.name}'"}

    # Verify user can access the specified channel_id
    if channel_id:
        target_channel = guild.get_channel(channel_id)
        if target_channel and not can_view_channel(target_channel):
            return {"error": "You don't have permission to access that channel"}

    # Resolve role_name to role_id if provided
    if role_name and not role_id:
        for r in guild.roles:
            if role_name.lower() in r.name.lower():
                role_id = r.id
                break

    if action == "messages":
        if not query:
            return {"error": "Query is required for message search"}
        # SECURITY: Pass accessible channels to filter results
        result = await discord_search_client.search_messages(
            guild_id=guild.id,
            query=query,
            channel_id=channel_id,
            author_id=user_id,
            has=has,
            before=before,
            after=after,
            limit=limit,
            accessible_channel_ids=accessible_channel_ids,
        )
        return result

    elif action == "members":
        return await discord_search_client.search_members(
            guild=guild,
            query=query,
            user_id=user_id,
            role_id=role_id,
            limit=limit,
        )

    elif action == "channels":
        # SECURITY: Pass permission filter to only show accessible channels
        return await discord_search_client.search_channels(
            guild=guild,
            query=query,
            channel_type=channel_type,
            limit=limit,
            can_view_channel=can_view_channel,
        )

    elif action == "threads":
        # SECURITY: Pass permission filter to only show accessible threads
        return await discord_search_client.search_threads(
            guild=guild,
            query=query,
            include_archived=include_archived,
            limit=limit,
            can_view_channel=can_view_channel,
        )

    elif action == "roles":
        return await discord_search_client.search_roles(
            guild=guild,
            query=query,
            include_members=include_members,
            limit=limit,
        )

    else:
        return {
            "error": f"Unknown action: {action}",
            "valid_actions": ["messages", "members", "channels", "threads", "roles"],
        }
