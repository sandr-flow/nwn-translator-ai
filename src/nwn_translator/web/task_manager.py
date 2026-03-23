"""In-memory translation tasks, background execution, and SSE event queue."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional

from ..config import TranslationConfig, lang_suffix
from ..main import ModuleTranslator
from .database import SqliteTranslationLogWriter, create_task_row, update_task_row, get_db

logger = logging.getLogger(__name__)

# Max upload size (bytes) — must match Starlette limit in routes
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

DEFAULT_TASK_TTL_SECONDS = 24 * 3600


@dataclass
class TranslationTask:
    """One translation job."""

    task_id: str
    client_ip: str
    client_token: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "pending"
    progress: float = 0.0
    phase: Optional[str] = None
    current_file: Optional[str] = None
    result_path: Optional[Path] = None
    extract_dir: Optional[Path] = None
    input_path: Optional[Path] = None
    error: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None
    input_filename: str = ""
    #: Thread-safe queue for SSE (worker thread -> async reader)
    event_queue: "Queue[Dict[str, Any]]" = field(default_factory=Queue)
    _done: threading.Event = field(default_factory=threading.Event)

    def mark_done(self) -> None:
        """Signal that the worker thread has finished processing."""
        self._done.set()

    def is_finished(self) -> bool:
        """Return ``True`` if the task has reached a terminal status."""
        return self.status in ("completed", "failed")


class TaskManager:
    """Stores tasks, enforces one active job per IP, TTL cleanup."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        task_ttl_seconds: float = DEFAULT_TASK_TTL_SECONDS,
    ) -> None:
        self.workspace_root = (
            Path(workspace_root) if workspace_root is not None else Path("workspace") / "web"
        )
        self.task_ttl_seconds = task_ttl_seconds
        self._tasks: Dict[str, TranslationTask] = {}
        self._lock = threading.Lock()
        #: IP -> task_id while job is running (not completed/failed)
        self._active_by_ip: Dict[str, str] = {}

    def workspace_for_task(self, task_id: str) -> Path:
        """Return (and create) the workspace directory for a given task.

        Args:
            task_id: UUID of the translation task.

        Returns:
            Path to the task's workspace directory.
        """
        path = self.workspace_root / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get(self, task_id: str) -> Optional[TranslationTask]:
        """Look up a task by ID (thread-safe).

        Args:
            task_id: UUID of the translation task.

        Returns:
            The task, or ``None`` if not found.
        """
        with self._lock:
            return self._tasks.get(task_id)

    def active_task_id_for_ip(self, ip: str) -> Optional[str]:
        """Return the active (non-finished) task ID for *ip*, or ``None``.

        Args:
            ip: Client IP address.

        Returns:
            Task ID string if an active task exists, else ``None``.
        """
        with self._lock:
            tid = self._active_by_ip.get(ip)
            if not tid:
                return None
            t = self._tasks.get(tid)
            if t and not t.is_finished():
                return tid
            return None

    def create_task(
        self,
        client_ip: str,
        input_filename: str,
        client_token: str = "",
        target_lang: Optional[str] = None,
        source_lang: Optional[str] = None,
        model: Optional[str] = None,
    ) -> TranslationTask:
        """Create and register a new translation task.

        Args:
            client_ip: Originating client IP address.
            input_filename: Original uploaded filename.
            client_token: Anonymous client UUID from localStorage.
            target_lang: Target translation language.
            source_lang: Source language.
            model: Model slug used for translation.

        Returns:
            Newly created ``TranslationTask``.
        """
        task_id = str(uuid.uuid4())
        task = TranslationTask(
            task_id=task_id,
            client_ip=client_ip,
            client_token=client_token,
            input_filename=input_filename,
        )
        with self._lock:
            self._tasks[task_id] = task
        create_task_row(
            task_id=task_id,
            client_token=client_token,
            client_ip=client_ip,
            created_at=task.created_at,
            input_filename=input_filename,
            target_lang=target_lang,
            source_lang=source_lang,
            model=model,
        )
        return task

    def register_active(self, client_ip: str, task_id: str) -> None:
        """Mark *task_id* as the active job for *client_ip*.

        Args:
            client_ip: Client IP address.
            task_id: UUID of the task to register.
        """
        with self._lock:
            self._active_by_ip[client_ip] = task_id

    def release_active(self, client_ip: str, task_id: str) -> None:
        """Remove the active-job mapping for *client_ip* if it matches *task_id*.

        Args:
            client_ip: Client IP address.
            task_id: UUID of the task to release.
        """
        with self._lock:
            if self._active_by_ip.get(client_ip) == task_id:
                del self._active_by_ip[client_ip]

    def _push_event(self, task: TranslationTask, payload: Dict[str, Any]) -> None:
        """Enqueue an SSE event payload for the task's event stream.

        Args:
            task: Target translation task.
            payload: JSON-serializable event dict.
        """
        task.event_queue.put(payload)

    # Phase -> (start_pct, end_pct) for weighted global progress.
    _PHASE_WEIGHTS = {
        "extracting":        (0.0,  0.02),
        "scanning":          (0.02, 0.05),
        "extracting_content":(0.05, 0.15),
        "translating":       (0.15, 0.85),
        "translating_item":  (0.15, 0.85),
        "injecting":         (0.85, 0.95),
        "building":          (0.95, 1.0),
    }

    def _make_progress_callback(self, task: TranslationTask) -> Callable[..., None]:
        """Create a progress callback that updates *task* state and pushes SSE events.

        Progress is weighted across phases and guaranteed to be monotonically
        increasing so the progress bar never jumps backwards.
        """
        def callback(
            phase: str,
            current: int,
            total: int,
            message: Optional[str] = None,
        ) -> None:
            task.phase = phase
            task.status = phase if phase in ("extracting", "scanning", "translating", "building") else task.status
            task.current_file = message

            start, end = self._PHASE_WEIGHTS.get(phase, (0.0, 1.0))
            local = (current / total) if total else 0.0
            weighted = start + (end - start) * local
            task.progress = max(task.progress, weighted)

            self._push_event(
                task,
                {
                    "type": "progress",
                    "phase": phase,
                    "current": current,
                    "total": total,
                    "file": message,
                    "progress": task.progress,
                },
            )

        return callback

    def run_translation_in_thread(
        self,
        task: TranslationTask,
        *,
        api_key: str,
        target_lang: str,
        source_lang: str,
        model: Optional[str],
        preserve_tokens: bool,
        use_context: bool,
        max_concurrent_requests: int,
        player_gender: str,
        input_path: Path,
    ) -> None:
        """Run ModuleTranslator in a worker thread (call via asyncio.to_thread)."""
        base = self.workspace_for_task(task.task_id)
        temp_dir = base / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        lang_suf = lang_suffix(target_lang)
        output_file = base / f"{input_path.stem}{lang_suf}{input_path.suffix}"

        log_writer = SqliteTranslationLogWriter(task.task_id)

        progress_cb = self._make_progress_callback(task)
        task.input_path = input_path
        update_task_row(task.task_id, input_path=str(input_path))

        try:
            task.status = "extracting"
            self._push_event(task, {"type": "status", "status": "extracting"})
            update_task_row(task.task_id, status="extracting")

            config = TranslationConfig(
                api_key=api_key,
                model=model,
                source_lang=source_lang,
                target_lang=target_lang,
                input_file=input_path,
                output_file=output_file,
                translation_log=None,
                translation_log_writer=log_writer,
                temp_dir=temp_dir,
                skip_cleanup=True,
                preserve_tokens=preserve_tokens,
                use_context=use_context,
                max_concurrent_requests=max(1, int(max_concurrent_requests)),
                player_gender=player_gender,
                tlk_file=None,
                verbose=False,
                quiet=True,
                progress_callback=progress_cb,
            )

            if not config.input_file.exists():
                raise ValueError(f"Input file not found: {config.input_file}")

            suffix = config.input_file.suffix.lower()
            if suffix not in (".mod", ".erf", ".hak"):
                raise ValueError("Input must be a .mod, .erf, or .hak file")

            config.api_key = config.get_api_key()

            translator = ModuleTranslator(config)
            result_path = translator.translate()
            task.result_path = Path(result_path)
            task.extract_dir = translator.extract_dir
            task.stats = translator.get_statistics()
            # Replace opaque "items_translated" with actual per-file count from DB
            try:
                db = get_db()
                row = db.execute(
                    "SELECT COUNT(*) FROM translations WHERE task_id = ?",
                    (task.task_id,),
                ).fetchone()
                task.stats["texts_translated"] = row[0] if row else 0
            except Exception:
                pass
            task.progress = 1.0
            self._push_event(
                task,
                {
                    "type": "completed",
                    "result_filename": task.result_path.name,
                    "stats": task.stats,
                },
            )
            task.status = "completed"
            update_task_row(
                task.task_id,
                status="completed",
                result_path=str(task.result_path),
                extract_dir=str(task.extract_dir),
                stats=task.stats,
            )
        except Exception as e:
            logger.exception("Translation failed for task %s", task.task_id)
            task.error = str(e)
            self._push_event(task, {"type": "failed", "error": str(e)})
            task.status = "failed"
            update_task_row(task.task_id, status="failed", error=str(e))
        finally:
            task.mark_done()
            self.release_active(task.client_ip, task.task_id)

    def purge_expired(self) -> None:
        """Remove finished tasks from in-memory dict to free RAM.

        Workspace files and DB rows are kept — the user deletes via UI.
        """
        now = time.time()
        with self._lock:
            to_delete: List[str] = []
            for tid, t in self._tasks.items():
                if now - t.created_at > self.task_ttl_seconds and t.is_finished():
                    to_delete.append(tid)
            for tid in to_delete:
                self._tasks.pop(tid, None)


# Global manager instance (tests can replace)
_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Return the global ``TaskManager`` singleton, creating it on first call."""
    global _manager
    if _manager is None:
        root_env = os.environ.get("NWN_WEB_TASK_ROOT", "").strip()
        root = Path(root_env) if root_env else None
        _manager = TaskManager(workspace_root=root)
    return _manager


def set_task_manager(m: Optional[TaskManager]) -> None:
    """Replace the global ``TaskManager`` (useful for tests).

    Args:
        m: New manager instance, or ``None`` to reset.
    """
    global _manager
    _manager = m


async def purge_loop_task_manager(interval_seconds: float = 3600) -> None:
    """Background loop to purge expired tasks."""
    while True:
        await asyncio.sleep(interval_seconds)
        get_task_manager().purge_expired()
