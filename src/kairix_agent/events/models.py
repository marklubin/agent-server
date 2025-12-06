"""SQLAlchemy models for agent events with Postgres LISTEN/NOTIFY trigger."""

from datetime import datetime
from enum import Enum
from uuid import uuid4

from sqlalchemy import DateTime, String, event
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import DDL


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""


class EventType(str, Enum):
    """Types of background events emitted by worker jobs."""

    SESSION_BOUNDARY_DETECTED = "session_boundary_detected"
    SUMMARY_COMPLETE = "summary_complete"
    INSIGHTS_COMPLETE = "insights_complete"


class AgentEvent(Base):
    """Persistent record of background events for an agent.

    Events are stored in Postgres and trigger pg_notify on insert,
    allowing the server to push real-time updates to connected clients.
    """

    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # type: ignore[type-arg]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
    )


# =============================================================================
# Postgres LISTEN/NOTIFY Trigger
# =============================================================================
# These DDL statements create a trigger that fires pg_notify on every INSERT,
# allowing the server to receive real-time notifications via asyncpg LISTEN.

_notify_function = DDL("""
    CREATE OR REPLACE FUNCTION notify_agent_event() RETURNS trigger AS $$
    BEGIN
        PERFORM pg_notify('agent_events', jsonb_build_object(
            'id', NEW.id::text,
            'agent_id', NEW.agent_id,
            'event_type', NEW.event_type,
            'payload', NEW.payload,
            'created_at', NEW.created_at::text
        )::text);
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
""")

_notify_trigger = DDL("""
    CREATE TRIGGER agent_event_notify
        AFTER INSERT ON agent_events
        FOR EACH ROW EXECUTE FUNCTION notify_agent_event();
""")

# Attach DDL to table's after_create event (runs during migrations/create_all)
event.listen(
    AgentEvent.__table__,
    "after_create",
    _notify_function.execute_if(dialect="postgresql"),
)
event.listen(
    AgentEvent.__table__,
    "after_create",
    _notify_trigger.execute_if(dialect="postgresql"),
)
