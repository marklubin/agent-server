"""Background insights job.

Periodically checks if an active conversation is happening and sends recent
messages to the background insights agent to evaluate/update the insights block.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from letta_client import AsyncLetta
from letta_client.types.agents import (
    AssistantMessage,
    ReasoningMessage,
    SystemMessage,
    ToolCallMessage,
    UserMessage,
)

from kairix_agent.agent_config import get_agent_config
from kairix_agent.config import Config
from kairix_agent.events import EventType, publish_event

if TYPE_CHECKING:
    from saq.types import Context

logger = logging.getLogger(__name__)

RECENT_MESSAGE_COUNT = 10


def _format_messages_for_insights(
    messages: list[
        UserMessage | AssistantMessage | ReasoningMessage | ToolCallMessage | SystemMessage
    ],
) -> str:
    """Format messages into a prompt for the insights agent.

    Args:
        messages: Recent messages from conversational agent.

    Returns:
        Formatted conversation string.
    """
    formatted: list[str] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            continue
        if isinstance(msg, UserMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            formatted.append(f"[user]: {content}")
        elif isinstance(msg, AssistantMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            formatted.append(f"[assistant]: {content}")
        elif isinstance(msg, ReasoningMessage):
            formatted.append(f"[reasoning]: {msg.reasoning}")
        else:
            tool_name = getattr(msg.tool_call, "name", "unknown_tool")
            formatted.append(f"[tool_call]: {tool_name}")

    return "\n".join(formatted) if formatted else "(no messages)"


async def _check_agent_insights(
    client: AsyncLetta,
    agent_id: str,
    insights_agent_id: str,
) -> dict[str, object]:
    """Check and potentially update insights for a single agent.

    Args:
        client: Letta client.
        agent_id: Conversational agent ID.
        insights_agent_id: Background insights agent ID.

    Returns:
        Status dict.
    """
    # Pull all messages and take last N (Letta API ignores order=desc)
    all_messages: list[Any] = [
        msg
        async for msg in client.agents.messages.list(
            agent_id=agent_id,
            order="asc",
            order_by="created_at",
        )
    ]

    if len(all_messages) > 1000:
        msg = f"Agent {agent_id} has {len(all_messages)} messages - need to implement proper pagination"
        raise RuntimeError(msg)

    # Take last N messages (most recent, in chronological order)
    messages = all_messages[-RECENT_MESSAGE_COUNT:] if all_messages else []

    if not messages:
        logger.info("No messages for agent %s, skipping insights check", agent_id)
        return {"status": "skipped", "reason": "no_messages"}

    # Debug: dump message IDs and timestamps
    logger.info(
        "Got %d messages for agent %s (from %d total):", len(messages), agent_id, len(all_messages)
    )
    for i, m in enumerate(messages[-5:]):
        logger.info("  [%d] %s | %s | %s", i, m.id, m.date, m.message_type)

    # Check if newest message is recent enough (within session gap)
    # messages[-1] is newest since we're in chronological order
    last_message = messages[-1]
    last_message_time = last_message.date
    now = datetime.now(tz=UTC)
    gap = now - last_message_time
    session_gap_minutes = Config.SESSION_GAP_MINUTES.value

    if gap >= timedelta(minutes=session_gap_minutes):
        logger.debug(
            "No active conversation for agent %s (gap: %s >= %s minutes), skipping",
            agent_id,
            gap,
            session_gap_minutes,
        )
        # Publish event with triggered=False (no active conversation)
        await publish_event(
            agent_id=agent_id,
            event_type=EventType.INSIGHTS_COMPLETE,
            payload={
                "triggered": False,
                "response": None,
            },
        )
        return {
            "status": "skipped",
            "reason": "no_active_conversation",
            "gap_seconds": gap.total_seconds(),
        }

    # Active conversation - messages already in chronological order
    conversation_text = _format_messages_for_insights(messages)

    prompt = f"""Review the current conversation and determine if your background_insights block needs updating.

<recent_conversation>
{conversation_text}
</recent_conversation>

Your background_insights block is visible in your memory. Evaluate whether it supports this conversation:
1. If relevant: respond briefly acknowledging no update needed
2. If stale/irrelevant:
   - Search archival memory for relevant context
   - Optionally search the web for current information
   - Update the background_insights block using core_memory_replace

Remember: only update if truly necessary. Irrelevant updates add noise."""

    logger.info(
        "Sending %d messages to insights agent %s for evaluation", len(messages), insights_agent_id
    )

    response = await client.agents.messages.create(
        agent_id=insights_agent_id,
        input=prompt,
    )

    # Extract response text
    response_text = ""
    for msg in response.messages:
        if isinstance(msg, AssistantMessage) and msg.content:
            if isinstance(msg.content, str):
                response_text += msg.content
            else:
                for item in msg.content:
                    if hasattr(item, "text"):
                        response_text += item.text

    logger.info(
        "Insights agent response (%d chars): %s...",
        len(response_text),
        response_text[:100] if response_text else "(empty)",
    )

    # Reset insights agent to prevent context buildup
    await client.agents.messages.reset(agent_id=insights_agent_id)
    logger.debug("Reset insights agent %s message history", insights_agent_id)

    # Publish event for connected clients
    await publish_event(
        agent_id=agent_id,
        event_type=EventType.INSIGHTS_COMPLETE,
        payload={
            "triggered": True,
            "response": response_text,
        },
    )
    logger.info("Published INSIGHTS_COMPLETE event for agent %s", agent_id)

    return {
        "status": "ok",
        "messages_checked": len(messages),
        "response_length": len(response_text),
    }


async def check_insights_relevance(
    _ctx: Context,
    *,
    agents: list[dict[str, Any]],
) -> dict[str, object]:
    """Check if background insights need updating for monitored agents.

    This job runs every minute. For each agent:
    1. Pull last 10 messages
    2. If last message is older than SESSION_GAP_MINUTES, skip (no active conversation)
    3. Otherwise, send messages to insights agent for evaluation
    4. Reset insights agent after each run

    Args:
        _ctx: SAQ job context.
        agents: List of agent configs with agent_id and letta_url.

    Returns:
        Status dict with results per agent.
    """
    if not agents:
        logger.warning("No agents configured, skipping insights check")
        return {"status": "skipped", "reason": "no_agents_configured"}

    results: dict[str, object] = {}

    for agent_cfg in agents:
        agent_id = agent_cfg["agent_id"]
        letta_url = agent_cfg["letta_url"]

        try:
            # Load agent config to get insights_agent_id
            config = await get_agent_config(agent_id=agent_id, letta_url=letta_url)

            if not config.insights_agent_id:
                logger.debug("No insights agent configured for %s, skipping", agent_id)
                results[agent_id] = {"status": "skipped", "reason": "no_insights_agent"}
                continue

            client = AsyncLetta(base_url=letta_url)
            result = await _check_agent_insights(
                client=client,
                agent_id=agent_id,
                insights_agent_id=config.insights_agent_id,
            )
            results[agent_id] = result

        except Exception:
            logger.exception("Error checking insights for agent %s", agent_id)
            results[agent_id] = {"status": "error", "reason": "exception"}

    return {"status": "ok", "agents": results}
