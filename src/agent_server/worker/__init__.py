"""Background worker package using SAQ."""

from saq import CronJob, Queue

from agent_server.worker.jobs import heartbeat_job

# Redis URL - localhost for local dev
REDIS_URL = "redis://localhost:6379"

queue = Queue.from_url(REDIS_URL)

settings = {
    "queue": queue,
    "functions": [heartbeat_job],
    "concurrency": 5,
    "cron_jobs": [
        CronJob(heartbeat_job, cron="* * * * * */10"),  # Every 10 seconds
    ],
}
