"""Session boundary detection job."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from saq import Queue

from agent_server.config import Config
from agent_server.memory import CursorStore, LettaMemoryService, SummarizationCursor

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from saq.types import Context

logger = logging.getLogger(__name__)


async def check_session_boundaries(ctx: Context) -> dict[str, object]:
    """Check for completed sessions that need summarization.

    Session boundary detection logic:
    1. Get cursor (last summarized message ID)
    2. Fetch messages since cursor
    3. If messages exist AND last message was > SESSION_GAP_MINUTES ago,
       we have a completed session ready for summarization
    4. Enqueue summarization job for that session

    Args:
        ctx: SAQ job context (contains queue reference for enqueueing).

    Returns:
        Status dict with detection results.
    """
    if not Config.LETTA_AGENT_ID.value:
        logger.warning("LETTA_AGENT_ID not configured, skipping session check")
        return {"status": "skipped", "reason": "no agent configured"}

    # Get Redis from SAQ queue
    queue_obj = ctx.get("queue")
    if not isinstance(queue_obj, Queue):
        logger.error("No queue in context")
        return {"status": "error", "reason": "no queue in context"}

    queue: Queue = queue_obj

    # Create services
    redis: Redis = queue.redis  # type: ignore[type-arg, assignment]
    cursor_store = CursorStore(redis)
    agent_id = Config.LETTA_AGENT_ID.value
    memory_service = LettaMemoryService(
        agent_id=agent_id,
        archive_id=Config.LETTA_ARCHIVE_ID.value,
        base_url=Config.LETTA_BASE_URL.value,
    )

    # Get current cursor
    cursor = await cursor_store.get_cursor(agent_id)
    after_message_id = cursor.last_message_id if cursor else None

    # Collect messages since cursor
    messages = [
        message
        async for message in memory_service.get_messages_since(after_message_id)
    ]

    if not messages:
        logger.debug("No new messages for agent %s", agent_id)
        return {"status": "ok", "messages_found": 0}

    # Check if last message is old enough (session gap exceeded)
    last_message = messages[-1]
    last_message_time = last_message.date
    now = datetime.now(tz=UTC)
    gap = now - last_message_time
    session_gap_minutes = Config.SESSION_GAP_MINUTES.value

    if gap < timedelta(minutes=session_gap_minutes):
        logger.debug(
            "Session still active for agent %s (gap: %s < %s minutes)",
            agent_id,
            gap,
            session_gap_minutes,
        )
        return {
            "status": "ok",
            "messages_found": len(messages),
            "session_active": True,
            "gap_seconds": gap.total_seconds(),
        }

    # We have a completed session! Enqueue summarization
    logger.info(
        "Detected session boundary for agent %s: %d messages, gap %s",
        agent_id,
        len(messages),
        gap,
    )

    # Extract message IDs for the session
    message_ids = [m.id for m in messages]
    first_message = messages[0]

    # Enqueue summarization job
    await queue.enqueue(
        "summarize_session",
        agent_id=agent_id,
        archive_id=Config.LETTA_ARCHIVE_ID.value,
        message_ids=message_ids,
        period_start=first_message.date.isoformat(),
        period_end=last_message.date.isoformat(),
    )

    # Update cursor to last message
    new_cursor = SummarizationCursor(
        agent_id=agent_id,
        last_summarized_at=now,
        last_message_id=last_message.id,
    )
    await cursor_store.set_cursor(new_cursor)

    return {
        "status": "ok",
        "messages_found": len(messages),
        "session_complete": True,
        "summarization_enqueued": True,
    }
