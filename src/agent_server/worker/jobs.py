"""Background job definitions."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def heartbeat_job(ctx: dict[str, object]) -> dict[str, str]:
    """Simple heartbeat job that logs every invocation."""
    now = datetime.now(tz=timezone.utc).isoformat()
    logger.info("Heartbeat: worker is alive at %s", now)
    print(f"[HEARTBEAT] Worker is alive at {now}")  # noqa: T201
    return {"status": "ok"}
