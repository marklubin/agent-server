"""SAQ worker settings module.

This module exposes the settings dict for SAQ to consume.
Run with: saq kairix_agent.worker.settings
"""

from __future__ import annotations

import logging

from saq import CronJob, Queue

from kairix_agent.config import Config
from kairix_agent.logging_config import setup_logging
from kairix_agent.worker.jobs import (
    check_insights_relevance,
    check_session_boundaries,
    summarize_session,
)

# Configure logging before anything else
setup_logging("worker")

logger = logging.getLogger(__name__)


queue = Queue.from_url(Config.REDIS_URL.value)
logger.info("Created queue: %s (redis_url=%s)", queue, Config.REDIS_URL.value)

# Explicit agent configuration - not loaded from env vars
# This allows monitoring multiple agents simultaneously
MONITORED_AGENTS = [
    {
        "agent_id": "agent-62f4b273-69c4-41d3-8571-02a0413756fb",  # Corindel
        "letta_url": "http://localhost:9000",
    },
]

# Job-specific timeout settings (in seconds)
# summarize_session can take a while due to LLM calls
JOB_TIMEOUTS = {
    "summarize_session": 300,  # 5 minutes for summarization
    "check_session_boundaries": 60,  # 1 minute for session boundary check
    "check_insights_relevance": 60,  # 1 minute max (less than cron interval)
}

settings = {
    "queue": queue,
    "functions": [check_insights_relevance, check_session_boundaries, summarize_session],
    "concurrency": 5,
    "cron_jobs": [
        CronJob(
            check_session_boundaries,
            cron="* * * * *",  # Every minute
            kwargs={"agents": MONITORED_AGENTS},
            timeout=JOB_TIMEOUTS["check_session_boundaries"],
        ),
        CronJob(
            check_insights_relevance,
            cron="* * * * *",  # Every minute
            kwargs={"agents": MONITORED_AGENTS},
            timeout=JOB_TIMEOUTS["check_insights_relevance"],
        ),
    ],
}
