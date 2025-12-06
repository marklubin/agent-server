"""Event publisher for background worker jobs."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kairix_agent.config import Config
from kairix_agent.events.models import AgentEvent, EventType

# Async engine and session factory (lazy initialized)
_engine = create_async_engine(Config.DATABASE_URL.value, echo=False)
_async_session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def publish_event(
    agent_id: str,
    event_type: EventType,
    payload: dict | None = None,  # type: ignore[type-arg]
) -> AgentEvent:
    """Publish an event by inserting into Postgres.

    The database trigger handles pg_notify automatically on commit.

    Args:
        agent_id: The agent this event belongs to.
        event_type: Type of event (from EventType enum).
        payload: Optional event-specific data.

    Returns:
        The created AgentEvent record.
    """
    async with _async_session() as session:
        event = AgentEvent(
            agent_id=agent_id,
            event_type=event_type.value,
            payload=payload or {},
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        return event
