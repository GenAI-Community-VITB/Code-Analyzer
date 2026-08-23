from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from logging_config import truncate

MAX_LOG_ENTRIES = 50

_log_buffer: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_ENTRIES)
_lock = Lock()


def add_log_entry(
    prompt: str,
    action: str,
    status: str,
    final_score: float,
    *,
    bert_score: float | None = None,
    llm_score: float | None = None,
    iterations: int | None = None,
) -> dict[str, Any]:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": truncate(prompt, 120),
        "action": action,
        "status": status,
        "final_score": round(final_score, 2),
        "bert_score": round(bert_score, 2) if bert_score is not None else None,
        "llm_score": round(llm_score, 2) if llm_score is not None else None,
        "iterations": iterations,
    }
    with _lock:
        _log_buffer.appendleft(entry)
    return entry


def get_logs(limit: int = MAX_LOG_ENTRIES) -> list[dict[str, Any]]:
    with _lock:
        return list(_log_buffer)[:limit]
