"""In-process background worker for artifact analysis jobs."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

logger = logging.getLogger(__name__)

_SENTINEL = object()

_job_queue: queue.Queue[Any] = queue.Queue()
_worker_thread: threading.Thread | None = None
_llm_config: Any = None


def start_worker(llm_config: Any) -> None:
    """Start the singleton background analysis worker."""
    global _worker_thread, _llm_config
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _llm_config = llm_config
    _worker_thread = threading.Thread(target=_worker_loop, name="artifact-analysis-worker", daemon=True)
    _worker_thread.start()
    logger.info("Artifact analysis worker started.")


def stop_worker() -> None:
    """Signal the worker to stop and wait for it to finish."""
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        return
    _job_queue.put(_SENTINEL)
    _worker_thread.join(timeout=5.0)
    _worker_thread = None
    logger.info("Artifact analysis worker stopped.")


def enqueue_analysis(artifact_id: str) -> None:
    """Add an artifact to the analysis queue."""
    _job_queue.put(artifact_id)


def _worker_loop() -> None:
    from engine.artifact_analysis import run_analysis_for_worker

    while True:
        item = _job_queue.get()
        if item is _SENTINEL:
            break
        artifact_id: str = item
        try:
            run_analysis_for_worker(artifact_id, _llm_config)
        except Exception:
            logger.exception("Unhandled error in artifact analysis worker for artifact %s", artifact_id)
        finally:
            _job_queue.task_done()
