"""Session summarization job."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from letta_client import AsyncLetta
from letta_client.types.agents.assistant_message import AssistantMessage

from agent_server.config import Config

if TYPE_CHECKING:
    from saq.types import Context

logger = logging.getLogger(__name__)


async def _find_reflector_agent(client: AsyncLetta, conversational_agent_id: str) -> str | None:
    """Find the reflector agent for a given conversational agent.

    Uses naming convention: {agent_name} -> {agent_name}-Reflector

    Args:
        client: Letta client.
        conversational_agent_id: The conversational agent's ID.

    Returns:
        The reflector agent ID if found, None otherwise.
    """
    # Get the conversational agent's name
    conv_agent = await client.agents.retrieve(conversational_agent_id)
    if not conv_agent.name:
        logger.warning("Conversational agent %s has no name", conversational_agent_id)
        return None

    reflector_name = f"{conv_agent.name}-Reflector"

    # Search for reflector by name
    async for agent in client.agents.list(name=reflector_name):
        logger.info("Found reflector agent: %s (%s)", agent.name, agent.id)
        return agent.id

    logger.warning("No reflector agent found with name: %s", reflector_name)
    return None


async def _format_session_for_reflector(
    client: AsyncLetta,
    agent_id: str,
    message_ids: list[str],
    period_start: str,
    period_end: str,
) -> str:
    """Format session messages into a prompt for the reflector agent.

    Args:
        client: Letta client.
        agent_id: The conversational agent ID.
        message_ids: List of message IDs in the session.
        period_start: ISO timestamp of session start.
        period_end: ISO timestamp of session end.

    Returns:
        Formatted prompt string for the reflector.
    """
    # Fetch the actual message content
    messages: list[str] = []
    async for msg in client.agents.messages.list(agent_id):
        if msg.id in message_ids:
            # Extract text content based on message type
            role = getattr(msg, "role", "unknown")
            content = getattr(msg, "content", "")
            if content:
                messages.append(f"[{role}]: {content}")

    session_transcript = "\n".join(messages) if messages else "(no messages retrieved)"

    return f"""Please summarize the following conversation session.

Session Period: {period_start} to {period_end}
Message Count: {len(message_ids)}

<session_transcript>
{session_transcript}
</session_transcript>

Create a semantically rich summary that:
1. Captures the main topics, decisions, and insights
2. Notes any action items or commitments made
3. Identifies emotional tone and relationship dynamics
4. Includes specific details useful for future retrieval
5. Is written to maximize semantic searchability

Before writing your summary, search archival memory for related past sessions \
that might provide context."""


async def summarize_session(
    _ctx: Context,
    *,
    agent_id: str,
    message_ids: list[str],
    period_start: str,
    period_end: str,
) -> dict[str, object]:
    """Summarize a completed session and store in archival memory.

    This job:
    1. Finds the reflector agent by naming convention
    2. Sends session transcript to reflector for summarization
    3. Resets the reflector agent's message history
    4. Stores the summary in the conversational agent's archival memory
    5. Resets the conversational agent's message history

    Args:
        _ctx: SAQ job context.
        agent_id: The conversational Letta agent ID.
        message_ids: List of message IDs in the session.
        period_start: ISO timestamp of first message.
        period_end: ISO timestamp of last message.

    Returns:
        Status dict with summarization results.
    """
    logger.info(
        "Summarizing session for agent %s: %d messages from %s to %s",
        agent_id,
        len(message_ids),
        period_start,
        period_end,
    )

    client = AsyncLetta(base_url=Config.LETTA_BASE_URL.value)

    # 1. Find the reflector agent
    reflector_id = await _find_reflector_agent(client, agent_id)
    if not reflector_id:
        logger.error("Cannot summarize: no reflector agent found for %s", agent_id)
        return {
            "status": "error",
            "error": "reflector_not_found",
            "agent_id": agent_id,
        }

    # 2. Format session for reflector
    prompt = await _format_session_for_reflector(
        client, agent_id, message_ids, period_start, period_end
    )

    # 3. Send to reflector and get summary
    logger.info("Sending session to reflector %s for summarization", reflector_id)
    response = await client.agents.messages.create(
        agent_id=reflector_id,
        input=prompt,
    )

    # Extract summary from response - look for AssistantMessage with content
    summary_text = ""
    for msg in response.messages:
        if isinstance(msg, AssistantMessage) and msg.content:
            # content can be a string or list of content items
            if isinstance(msg.content, str):
                summary_text += msg.content
            else:
                # It's a list of content items, extract text from each
                for item in msg.content:
                    if hasattr(item, "text"):
                        summary_text += item.text

    if not summary_text:
        logger.warning("Reflector returned empty summary")
        return {
            "status": "error",
            "error": "empty_summary",
            "agent_id": agent_id,
        }

    logger.info("Received summary (%d chars) from reflector", len(summary_text))

    # 4. Reset the reflector agent to prevent context buildup
    # The reflector only needs current message + archival search, no persistent context
    await client.agents.messages.reset(agent_id=reflector_id)
    logger.info("Reset reflector agent %s message history", reflector_id)

    # 5. Store summary in archival memory (on the conversational agent)
    # First, get the agent's archive
    archive_id: str | None = None
    async for archive in client.archives.list(agent_id=agent_id):
        archive_id = archive.id
        break  # Use the first archive found

    if not archive_id:
        logger.warning("No archive found for agent %s, cannot store summary", agent_id)
        return {
            "status": "error",
            "error": "archive_not_found",
            "agent_id": agent_id,
        }

    await client.archives.passages.create(
        archive_id=archive_id,
        text=f"[Session Summary: {period_start} to {period_end}]\n\n{summary_text}",
    )
    logger.info("Stored summary in archival memory for agent %s", agent_id)

    # 6. Update the last_session_summary block in the conversational agent's core memory
    # This keeps the summary in-window for continuity after reset
    try:
        await client.agents.blocks.update(
            "last_session_summary",
            agent_id=agent_id,
            value=f"[Session: {period_start} to {period_end}]\n\n{summary_text}",
        )
        logger.info("Updated last_session_summary block for agent %s", agent_id)
    except Exception:
        logger.exception("Failed to update last_session_summary block")

    # 7. Reset the conversational agent's message history
    await client.agents.messages.reset(agent_id=agent_id)
    logger.info("Reset message history for agent %s", agent_id)

    return {
        "status": "ok",
        "agent_id": agent_id,
        "reflector_id": reflector_id,
        "message_count": len(message_ids),
        "period_start": period_start,
        "period_end": period_end,
        "summary_length": len(summary_text),
        "summary_stored": True,
    }
