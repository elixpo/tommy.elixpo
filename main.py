#!/usr/bin/env python3
"""
Polly Helper Bot - Discord bot for creating GitHub issues.

When @mentioned with an issue description, the bot uses AI to parse and enhance
the description, then creates a well-formatted GitHub issue.
"""

import logging
import sys

import discord

from src.config import config
from src.bot import bot


def setup_logging():
    """Configure logging for the application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    # Reduce noise from discord.py
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def main():
    """Entry point for the bot."""
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("Starting Polly Helper Bot...")

    # Validate configuration before starting
    config.validate()

    try:
        bot.run(config.discord_token, log_handler=None)
    except discord.errors.PrivilegedIntentsRequired:
        logger.error(
            "Privileged Intents Required!\n"
            "Enable 'Message Content Intent' in Discord Developer Portal:\n"
            "https://discord.com/developers/applications"
        )
        sys.exit(1)
    except discord.errors.LoginFailure:
        logger.error("Invalid Discord token. Check your DISCORD_TOKEN.")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
