"""Background worker package using SAQ."""

from saq import CronJob, Queue

from agent_server.config import Config
from agent_server.worker.jobs import (
    check_session_boundaries,
    heartbeat_job,
    summarize_session,
)

queue = Queue.from_url(Config.REDIS_URL.value)

settings = {
    "queue": queue,
    "functions": [heartbeat_job, check_session_boundaries, summarize_session],
    "concurrency": 5,
    "cron_jobs": [
        CronJob(heartbeat_job, cron="* * * * *"),  # Every minute
        CronJob(check_session_boundaries, cron="*/30 * * * * *"),  # Every 30 seconds
    ],
}
