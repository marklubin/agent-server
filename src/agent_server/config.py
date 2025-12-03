"""Centralized configuration from environment variables."""

import os
from enum import Enum


class Config(Enum):
    """Application configuration sourced from environment variables.

    Usage:
        Config.LETTA_BASE_URL.value  # Get the resolved value
        Config.LETTA_AGENT_ID.value  # Returns "" if not set
    """

    # Letta server configuration
    LETTA_BASE_URL = os.getenv("LETTA_BASE_URL", "http://localhost:9000")
    LETTA_AGENT_ID = os.getenv("LETTA_AGENT_ID", "")
    LETTA_ARCHIVE_ID = os.getenv("LETTA_ARCHIVE_ID", "")

    # Redis configuration
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

    # Session detection settings
    SESSION_GAP_MINUTES = int(os.getenv("SESSION_GAP_MINUTES", "1"))

    # External API keys
    DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
