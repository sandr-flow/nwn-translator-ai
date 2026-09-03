"""In-memory translation tasks, background execution, and SSE event queue."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import (
    TranslationCancelled,
    TranslationConfig,
    create_output_path,
    module_string_encoding_for_target_lang,
)
from ..main import ModuleTranslator, run_translation_pipeline

# ``ModuleTranslator`` stays imported here for test monkeypatch compatibility.
from .database import (
    TERMINAL_STATUSES,
    SqliteTranslationLogWriter,
    create_task_row,
    delete_task_row,
    get_db,
    get_finished_task_ids_older_than,
    get_unfinished_task_rows,
    update_task_row,
)

logger = logging.getLogger(__name__)

# Max upload size (bytes) — must match Starlette limit in routes
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

DEFAULT_TASK_TTL_SECONDS = 24 * 3600

#: Minimum seconds between SQLite writes of in-flight progress. The progress
#: callback fires per translated item — far too often to touch the DB every
#: time — but a phase change always persists immediately.
PROGRESS_PERSIST_INTERVAL_SECONDS = 2.0


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
    #: Language tags (mirrors SQLite tasks row; used by API status / rebuild fallback).
    target_lang: Optional[str] = None
    source_lang: Optional[str] = None
    #: Throttling state for persisting in-flight progress to SQLite.
    persisted_phase: Optional[str] = None
    last_persist_at: float = 0.0
    _done: threading.Event = field(default_factory=threading.Event)
    _cancel: threading.Event = field(default_factory=threading.Event)

    def mark_done(self) -> None:
        """Signal that the worker thread has finished processing."""
        self._done.set()

    def request_cancel(self) -> None:
        """Signal the worker thread to stop at the next safe point."""
        self._cancel.set()

    def is_cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def is_finished(self) -> bool:
        """Return ``True`` if the task has reached a terminal status."""
        return self.status in TERMINAL_STATUSES


class TaskManager:
    """Stores tasks, enforces one active job per IP, TTL cleanup."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        task_ttl_seconds: float = DEFAULT_TASK_TTL_SECONDS,
        db_connection: Optional[sqlite3.Connection] = None,
    ) -> None:
        self.workspace_root = (
            Path(workspace_root) if workspace_root is not None else Path("workspace") / "web"
        )
        self.task_ttl_seconds = task_ttl_seconds
        self._db_connection = db_connection
        self._tasks: Dict[str, TranslationTask] = {}
        self._lock = threading.Lock()
        #: IP -> task_id while job is running (not completed/failed)
        self._active_by_ip: Dict[str, str] = {}
        self._reconcile_interrupted()

    def _reconcile_interrupted(self) -> None:
        """Flip tasks left unfinished by a dead worker to ``interrupted``.

        A process restart leaves DB rows in a non-terminal status with no live
        worker. On startup we mark them ``interrupted`` (a terminal status) so
        clients stop seeing a forever-running job, and register them in memory so
        TTL purge can later drop them.
        """
        for row in get_unfinished_task_rows():
            update_task_row(row["task_id"], status="interrupted")
            task = TranslationTask(
                task_id=row["task_id"],
                client_ip=row["client_ip"],
                client_token=row.get("client_token", ""),
                created_at=row["created_at"],
                status="interrupted",
                input_filename=row.get("input_filename", ""),
                target_lang=row.get("target_lang"),
                source_lang=row.get("source_lang"),
            )
            task.mark_done()
            self._tasks[row["task_id"]] = task

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

    def active_task_count(self) -> int:
        """Return how many tasks have not reached a terminal status.

        Exposed via ``/api/health`` so the deploy can wait for an idle service
        before recreating the container: a restart kills every worker thread,
        and there is no resume.
        """
        with self._lock:
            return sum(1 for t in self._tasks.values() if not t.is_finished())

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
            target_lang=target_lang,
            source_lang=source_lang,
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

    def try_register_active(self, client_ip: str, task_id: str) -> bool:
        """Atomically register *task_id* for *client_ip* unless one is already active.

        The check and the registration happen in one critical section, so two
        concurrent requests from the same IP cannot both pass the one-job-per-IP
        limit.

        Args:
            client_ip: Client IP address.
            task_id: UUID of the task to register.

        Returns:
            ``True`` if registered; ``False`` if an unfinished task already
            occupies the slot for this IP.
        """
        with self._lock:
            existing = self._active_by_ip.get(client_ip)
            if existing:
                t = self._tasks.get(existing)
                if t and not t.is_finished():
                    return False
            self._active_by_ip[client_ip] = task_id
            return True

    def discard_task(self, task_id: str) -> None:
        """Remove a task that never started running (lost the IP race, failed upload).

        Drops it from memory and deletes its SQLite row so it does not linger in
        the client's history.

        Args:
            task_id: UUID of the task to discard.
        """
        with self._lock:
            self._tasks.pop(task_id, None)
        delete_task_row(task_id)

    def release_active(self, client_ip: str, task_id: str) -> None:
        """Remove the active-job mapping for *client_ip* if it matches *task_id*.

        Args:
            client_ip: Client IP address.
            task_id: UUID of the task to release.
        """
        with self._lock:
            if self._active_by_ip.get(client_ip) == task_id:
                del self._active_by_ip[client_ip]

    # Phase -> (start_pct, end_pct) for weighted global progress.
    # ``translating_item`` is the workhorse band (per-item granularity across
    # non-dialog and dialog translations); ``translating`` is kept as a brief
    # sentinel that marks entry into Phase B.
    _PHASE_WEIGHTS = {
        "extracting": (0.0, 0.03),
        "scanning": (0.03, 0.08),
        "extracting_content": (0.08, 0.12),
        "translating": (0.12, 0.14),
        "translating_item": (0.14, 0.88),
        "injecting": (0.88, 0.96),
        "building": (0.96, 1.0),
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
            # Do not clobber ``cancelling`` (or any post-cancel status) with a
            # phase name — otherwise SQLite looks "still translating" and the
            # client auto-resumes onto the progress screen after a refresh.
            if not task.is_cancel_requested() and phase in (
                "extracting",
                "scanning",
                "translating",
                "building",
            ):
                task.status = phase
            task.current_file = message

            start, end = self._PHASE_WEIGHTS.get(phase, (0.0, 1.0))
            local = (current / total) if total else 0.0
            weighted = start + (end - start) * local
            task.progress = max(task.progress, weighted)

            self._persist_progress(task, phase, message)

        return callback

    def _persist_progress(self, task: TranslationTask, phase: str, message: Optional[str]) -> None:
        """Mirror in-flight progress into SQLite, throttled by time.

        Without this the task row keeps the status it had at extraction time,
        so the history list and any client polling task status had no way to
        learn how far a running job has got.

        Args:
            task: Task whose current state should be persisted.
            phase: Pipeline phase reported by the callback.
            message: Current file or status message, if any.
        """
        now = time.time()
        stale = now - task.last_persist_at >= PROGRESS_PERSIST_INTERVAL_SECONDS
        if phase != task.persisted_phase or stale:
            task.persisted_phase = phase
            task.last_persist_at = now
            update_task_row(
                task.task_id,
                status=task.status,
                progress=task.progress,
                phase=phase,
                current_file=message,
            )

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
        reasoning_effort: Optional[str] = None,
        input_path: Path,
    ) -> None:
        """Run ModuleTranslator in a worker thread (call via asyncio.to_thread)."""
        base = self.workspace_for_task(task.task_id)
        temp_dir = base / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_file = create_output_path(input_path, target_lang, output_dir=base)

        log_writer = SqliteTranslationLogWriter(task.task_id)

        progress_cb = self._make_progress_callback(task)
        task.input_path = input_path
        task.target_lang = target_lang
        task.source_lang = source_lang
        update_task_row(task.task_id, input_path=str(input_path))

        try:
            logger.info(
                "Task %s: target_lang=%r source_lang=%r module_encoding=%s",
                task.task_id,
                target_lang,
                source_lang,
                module_string_encoding_for_target_lang(target_lang),
            )
            task.status = "extracting"
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
                reasoning_effort=reasoning_effort,
                verbose=False,
                quiet=True,
                progress_callback=progress_cb,
                cancel_check=task.is_cancel_requested,
            )

            result_path, translator = run_translation_pipeline(config)
            task.result_path = Path(result_path)
            task.extract_dir = translator.extract_dir
            task.stats = translator.get_statistics()
            # Replace opaque "items_translated" with actual per-file count from DB
            try:
                db = self._db_connection or get_db()
                row = db.execute(
                    "SELECT COUNT(*) FROM translations WHERE task_id = ?",
                    (task.task_id,),
                ).fetchone()
                task.stats["texts_translated"] = row[0] if row else 0
            except Exception:
                pass
            task.progress = 1.0
            task.phase = None
            task.current_file = None
            task.status = "completed"
            update_task_row(
                task.task_id,
                status="completed",
                progress=1.0,
                phase=None,
                current_file=None,
                result_path=str(task.result_path),
                extract_dir=str(task.extract_dir),
                stats=task.stats,
                updated_at=time.time(),
            )
        except TranslationCancelled:
            logger.info("Translation cancelled for task %s", task.task_id)
            task.status = "cancelled"
            task.error = None
            task.progress = 1.0
            task.phase = None
            task.current_file = None
            update_task_row(
                task.task_id,
                status="cancelled",
                progress=1.0,
                phase=None,
                current_file=None,
                updated_at=time.time(),
            )
        except Exception as e:
            logger.exception("Translation failed for task %s", task.task_id)
            task.error = str(e)
            task.status = "failed"
            task.progress = 1.0
            task.phase = None
            task.current_file = None
            update_task_row(
                task.task_id,
                status="failed",
                error=str(e),
                progress=1.0,
                phase=None,
                current_file=None,
                updated_at=time.time(),
            )
        finally:
            task.mark_done()
            self.release_active(task.client_ip, task.task_id)

    def purge_expired(self) -> None:
        """Evict finished tasks older than the TTL from memory and disk.

        Workspace directories (uploaded module, extraction temp, result) are
        deleted; DB rows and translations are kept, so the client history and
        the translation editor keep working while download/rebuild degrade to
        their existing "files unavailable" errors. Expired tasks come from the
        DB, not the in-memory dict: finished tasks are not reloaded into memory
        after a restart, but their workspace files survive it.
        """
        now = time.time()
        with self._lock:
            to_delete: List[str] = []
            for tid, t in self._tasks.items():
                if now - t.created_at > self.task_ttl_seconds and t.is_finished():
                    to_delete.append(tid)
            for tid in to_delete:
                self._tasks.pop(tid, None)

        # Filesystem work happens outside the lock.
        for tid in get_finished_task_ids_older_than(now - self.task_ttl_seconds):
            # Task IDs are our own uuid4 strings; refuse anything that could
            # escape workspace_root just in case a row was tampered with.
            if "/" in tid or "\\" in tid or tid in ("", ".", ".."):
                logger.warning("Skipping workspace purge for suspicious task id %r", tid)
                continue
            task_dir = self.workspace_root / tid
            if not task_dir.is_dir():
                continue
            try:
                shutil.rmtree(task_dir)
                logger.info("Purged expired workspace %s", task_dir)
            except OSError as e:
                logger.warning("Failed to purge workspace %s: %s", task_dir, e)


# Global manager instance (tests can replace)
_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Return the global ``TaskManager`` singleton, creating it on first call."""
    global _manager
    if _manager is None:
        root_env = os.environ.get("NWN_WEB_TASK_ROOT", "").strip()
        root = Path(root_env) if root_env else None
        _manager = TaskManager(workspace_root=root, db_connection=get_db())
    return _manager


def set_task_manager(m: Optional[TaskManager]) -> None:
    """Replace the global ``TaskManager`` (useful for tests).

    Args:
        m: New manager instance, or ``None`` to reset.
    """
    global _manager
    _manager = m


async def purge_loop_task_manager(
    task_manager: TaskManager, interval_seconds: float = 3600
) -> None:
    """Background loop to purge expired tasks."""
    while True:
        await asyncio.sleep(interval_seconds)
        task_manager.purge_expired()
