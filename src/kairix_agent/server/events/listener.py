"""Postgres LISTEN handler for agent events."""

import asyncio
import json
import logging

import asyncpg

from kairix_agent.config import Config
from kairix_agent.server.events.connection_manager import connection_manager

logger = logging.getLogger(__name__)


def _get_raw_postgres_url() -> str:
    """Convert asyncpg SQLAlchemy URL to raw postgres URL.

    SQLAlchemy uses: postgresql+asyncpg://user:pass@host/db
    asyncpg expects: postgresql://user:pass@host/db
    """
    url = Config.DATABASE_URL.value
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _handle_notification(
    connection: asyncpg.Connection,  # type: ignore[type-arg]  # noqa: ARG001
    pid: int,  # noqa: ARG001
    channel: str,  # noqa: ARG001
    payload: str,
) -> None:
    """Callback when Postgres sends a notification."""
    try:
        event_data = json.loads(payload)
        agent_id = event_data.get("agent_id")

        if agent_id:
            await connection_manager.dispatch(agent_id, event_data)
            logger.debug(
                "Dispatched %s event for agent %s",
                event_data.get("event_type"),
                agent_id,
            )
    except json.JSONDecodeError:
        logger.warning("Received invalid JSON in notification: %s", payload)
    except Exception:
        logger.exception("Error handling notification")


async def start_event_listener() -> None:
    """Start the Postgres LISTEN loop for agent events.

    This is a long-running task that should be started on server startup.
    It maintains a connection to Postgres and listens for notifications
    on the 'agent_events' channel, dispatching them to connected WebSocket
    clients via the ConnectionManager.
    """
    postgres_url = _get_raw_postgres_url()
    logger.info("Starting event listener, connecting to Postgres...")

    while True:
        try:
            conn = await asyncpg.connect(postgres_url)
            logger.info("Event listener connected, listening on 'agent_events' channel")

            await conn.add_listener("agent_events", _handle_notification)

            try:
                # Keep connection alive with periodic pings
                while True:
                    await asyncio.sleep(30)
                    # Simple keepalive query
                    await conn.execute("SELECT 1")
            finally:
                await conn.remove_listener("agent_events", _handle_notification)
                await conn.close()

        except asyncio.CancelledError:
            logger.info("Event listener shutting down")
            raise
        except Exception:
            logger.exception("Event listener connection error, reconnecting in 5s...")
            await asyncio.sleep(5)
