"""Heartbeat job for worker health monitoring."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saq.types import Context

logger = logging.getLogger(__name__)


async def heartbeat_job(_ctx: Context) -> dict[str, str]:
    """Simple heartbeat job that logs every invocation."""
    now = datetime.now(tz=UTC).isoformat()
    logger.info("Heartbeat: worker is alive at %s", now)
    return {"status": "ok"}
