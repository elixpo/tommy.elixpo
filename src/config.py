"""Configuration loading and validation for Polly Helper Bot."""

import os
import sys
import logging
from dotenv import load_dotenv
from .constants import DEFAULT_REPO

load_dotenv()

logger = logging.getLogger(__name__)


class Config:
    """Application configuration loaded from environment variables."""

    def _load_private_key(self) -> str:
        """Load GitHub App private key from env var or file path."""
        from pathlib import Path

        key_value = os.getenv("GITHUB_PRIVATE_KEY", "")
        if not key_value:
            return ""

        # Check if it's a file path (relative paths resolved from project root)
        key_path = Path(key_value)
        if not key_path.is_absolute():
            # Resolve relative to project root (where .env is)
            project_root = Path(__file__).parent.parent
            key_path = project_root / key_value

        if key_path.is_file():
            try:
                content = key_path.read_text()
                logger.info(f"Loaded private key from {key_path}")
                return content
            except Exception as e:
                logger.error(f"Failed to read private key file {key_path}: {e}")
                return ""

        # Otherwise treat as inline key with \n escapes
        return key_value.replace("\\n", "\n")

    def __init__(self):
        # Discord Configuration
        self.discord_token = os.getenv("DISCORD_TOKEN")

        # GitHub Configuration - supports both PAT and GitHub App
        # PAT (legacy/fallback)
        self.github_token = os.getenv("POLLI_PAT", "")

        # GitHub App (preferred for org repos)
        self.github_app_id = os.getenv("GITHUB_APP_ID", "")
        self.github_installation_id = os.getenv("GITHUB_INSTALLATION_ID", "")
        # Private key: can be inline (with \n escapes) or path to .pem file
        self.github_private_key = self._load_private_key()

        self.github_repo = os.getenv("GITHUB_REPO", DEFAULT_REPO)

        # GitHub Project PAT (required for ProjectV2 - GitHub Apps can't access projects)
        # Must have 'project' scope (classic PAT) or 'project:read' + 'project:write' (fine-grained)
        self.github_project_pat = os.getenv("GITHUB_PROJECT_PAT", "")

        # Pollinations API Configuration
        self.pollinations_token = os.getenv("POLLINATIONS_TOKEN", "")
        self.pollinations_model = os.getenv("POLLINATIONS_MODEL", "gemini-large")

        # Admin Configuration (optional - if not set, admin tools are disabled)
        # Supports multiple role IDs separated by commas for multi-server bots
        admin_roles = os.getenv("ADMIN_ROLE_ID", "")
        self.admin_role_ids = [int(x.strip()) for x in admin_roles.split(",") if x.strip().isdigit()]

        # Optional Features
        self.sandbox_enabled = os.getenv("SANDBOX_ENABLED", "false").lower() == "true"
        self.local_embeddings_enabled = os.getenv("LOCAL_EMBEDDINGS_ENABLED", "false").lower() == "true"
        self.embeddings_repo = os.getenv("EMBEDDINGS_REPO", "pollinations/pollinations")

    @property
    def use_github_app(self) -> bool:
        """Check if GitHub App credentials are configured."""
        return bool(
            self.github_app_id and
            self.github_installation_id and
            self.github_private_key
        )

    @property
    def has_project_access(self) -> bool:
        """Check if project PAT is configured for ProjectV2 access."""
        return bool(self.github_project_pat)

    def validate(self) -> bool:
        """
        Validate that all required configuration is present.

        Returns:
            True if valid, exits with error message if not.
        """
        errors = []

        if not self.discord_token:
            errors.append("DISCORD_TOKEN is required - get it from https://discord.com/developers/applications")

        # Need either GitHub App OR PAT
        if not self.use_github_app and not self.github_token:
            errors.append(
                "GitHub auth required. Either:\n"
                "  - Set GITHUB_APP_ID, GITHUB_INSTALLATION_ID, and GITHUB_PRIVATE_KEY (recommended for orgs)\n"
                "  - Or set POLLI_PAT (classic PAT with repo scope)"
            )

        if not self.pollinations_token:
            errors.append("POLLINATIONS_TOKEN is required - get it from enter.pollinations.ai")

        if errors:
            logger.error("Configuration errors:")
            for error in errors:
                logger.error(f"  - {error}")
            logger.error("\nPlease set up your .env file with the required credentials")
            sys.exit(1)

        # Log which auth method is being used
        if self.use_github_app:
            logger.info(f"Using GitHub App authentication (App ID: {self.github_app_id})")
        else:
            logger.info("Using GitHub PAT authentication")

        # Log project access status
        if self.has_project_access:
            logger.info("ProjectV2 access enabled (GITHUB_PROJECT_PAT configured)")
        else:
            logger.info("ProjectV2 access disabled (no GITHUB_PROJECT_PAT - GitHub Apps can't access projects)")

        return True


# Global config instance
config = Config()
