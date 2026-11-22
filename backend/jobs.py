from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from backend import database
from backend.pipeline.runner import run_pipeline

log = logging.getLogger(__name__)


class JobManager:
    def __init__(self) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._worker, name="clipper-job-worker", daemon=True)
            self._thread.start()
        # Recover unfinished jobs after an application restart.
        with database.connect() as conn:
            rows = conn.execute("SELECT id,status FROM jobs WHERE status IN ('queued','processing') ORDER BY created_at").fetchall()
        for row in rows:
            database.update_job(row["id"], status="queued", stage="queued", message="Resuming after application restart")
            self.enqueue(row["id"])

    def enqueue(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._active:
                return
            self._active.add(job_id)
        self._queue.put(job_id)

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                database.update_job(job_id, status="processing", stage="starting", progress=0.01, message="Starting analysis", error=None)
                def progress(stage: str, value: float, message: str) -> None:
                    database.update_job(job_id, status="processing", stage=stage, progress=value, message=message)
                run_pipeline(job_id, progress)
            except Exception as exc:
                log.exception("Job %s failed", job_id)
                database.update_job(job_id, status="failed", stage="failed", message="Processing failed", error=str(exc)[-2500:])
            finally:
                with self._lock:
                    self._active.discard(job_id)
                self._queue.task_done()


manager = JobManager()
