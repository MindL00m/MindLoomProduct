"""Shared in-process job registry used by ingestion and connection setup."""

from models import JobStatus

# V1 is intentionally in-memory. Durable workers can replace this registry
# without changing the HTTP contracts.
job_store: dict[str, JobStatus] = {}
