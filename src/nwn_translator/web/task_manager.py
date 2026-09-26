"""Translation jobs of the web service: task state, execution, rebuilds and cleanup.

:class:`TaskManager` is the service layer behind the HTTP routes. It keeps the
tasks of the running process in memory, mirrors their state into SQLite (see
:mod:`.database`) so history and polling survive restarts, allows one running
job per client IP, and purges old workspaces.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set

from ..config import (
    TranslationCancelled,
    TranslationConfig,
    create_output_path,
    module_string_encoding_for_target_lang,
)
from ..main import rebuild_module, run_translation_pipeline
from . import editor
from .database import (
    TERMINAL_STATUSES,
    SqliteTranslationLogWriter,
    count_translations,
    create_task_row,
    decode_stats,
    delete_task_row,
    get_finished_task_ids_older_than,
    get_item_translation_map_by_task,
    get_task_row,
    get_translations_by_task,
    get_unfinished_task_rows,
    update_task_row,
    update_translation_text,
)
from .schemas import RebuildEdit

logger = logging.getLogger(__name__)

DEFAULT_TASK_TTL_SECONDS = 24 * 3600

#: Seconds between two runs of :meth:`TaskManager.purge_expired`.
PURGE_INTERVAL_SECONDS = 3600

#: Minimum seconds between SQLite writes of in-flight progress. The progress
#: callback fires per translated item — far too often to touch the DB every
#: time — but a phase change always persists immediately.
PROGRESS_PERSIST_INTERVAL_SECONDS = 2.0

#: Share of the overall progress bar per pipeline phase, as ``(start, end)``.
#: ``translating_item`` carries the per-item progress of all translations;
#: ``translating`` only marks that translation has started.
_PHASE_WEIGHTS = {
    "extracting": (0.0, 0.03),
    "scanning": (0.03, 0.08),
    "extracting_content": (0.08, 0.12),
    "translating": (0.12, 0.14),
    "translating_item": (0.14, 0.88),
    "injecting": (0.88, 0.96),
    "building": (0.96, 1.0),
}

#: Phases that also become the task status shown in history and polling.
_STATUS_PHASES = frozenset({"extracting", "scanning", "translating", "building"})


@dataclass(frozen=True)
class JobParams:
    """Translation settings of one job, validated and normalized from the request.

    The field names are :class:`~nwn_translator.config.TranslationConfig` fields
    and are passed to it unchanged.

    Attributes:
        api_key: The client's provider API key.
        target_lang: Target language.
        source_lang: Source language, ``"auto"`` to detect.
        model: Model slug; ``None`` selects the provider default.
        preserve_tokens: Protect NWN tokens such as ``<FirstName>``.
        use_context: Build world context and glossary before translating.
        max_concurrent_requests: Parallel provider requests.
        player_gender: ``"male"`` or ``"female"``.
        reasoning_effort: Provider reasoning effort, ``None`` to omit it.
    """

    api_key: str
    target_lang: str
    source_lang: str
    model: Optional[str]
    preserve_tokens: bool
    use_context: bool
    max_concurrent_requests: int
    player_gender: str
    reasoning_effort: Optional[str]


def _optional_path(value: Optional[str]) -> Optional[Path]:
    """Path of a stored path column, ``None`` when empty."""
    return Path(value) if value else None


@dataclass
class TranslationTask:
    """State of one translation job.

    Attributes:
        task_id: Task UUID.
        client_ip: Address that started the job (one running job per IP).
        client_token: Anonymous owner token; empty for ownerless tasks.
        created_at: Unix timestamp of creation.
        status: ``pending``, a status phase, ``cancelling`` or a terminal status.
        progress: Weighted overall progress in ``[0, 1]``.
        phase: Pipeline phase last reported by the worker.
        current_file: File or message last reported by the worker.
        result_path: Translated module, once completed.
        extract_dir: Extracted resources kept for rebuilds.
        input_path: Uploaded module.
        error: Failure message.
        stats: Run statistics of a completed job.
        input_filename: Name of the uploaded module.
        target_lang: Target language.
        source_lang: Source language.
        persisted_phase: Phase of the last progress write to SQLite.
        last_persist_at: Time of the last progress write to SQLite.
    """

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
    target_lang: Optional[str] = None
    source_lang: Optional[str] = None
    persisted_phase: Optional[str] = None
    last_persist_at: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "TranslationTask":
        """Rebuild a task from its SQLite row (progress fields are not restored).

        Args:
            row: Row of the ``tasks`` table.

        Returns:
            The task.
        """
        return cls(
            task_id=row["task_id"],
            client_ip=row["client_ip"],
            client_token=row["client_token"],
            created_at=row["created_at"],
            status=row["status"],
            input_filename=row["input_filename"],
            result_path=_optional_path(row["result_path"]),
            extract_dir=_optional_path(row["extract_dir"]),
            input_path=_optional_path(row["input_path"]),
            target_lang=row["target_lang"],
            source_lang=row["source_lang"],
            error=row["error"],
            stats=decode_stats(row["stats"]),
        )

    def request_cancel(self) -> None:
        """Ask the worker to stop at its next cancellation checkpoint."""
        self._cancel.set()

    def is_cancel_requested(self) -> bool:
        """Whether cancellation has been requested."""
        return self._cancel.is_set()

    def is_finished(self) -> bool:
        """Whether the task has reached a terminal status."""
        return self.status in TERMINAL_STATUSES


class TaskManager:
    """Runs translation jobs and rebuilds, and owns the in-memory task registry.

    Attributes:
        workspace_root: Directory holding one workspace per task.
        task_ttl_seconds: Age after which a finished task's workspace is purged.
    """

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        task_ttl_seconds: float = DEFAULT_TASK_TTL_SECONDS,
    ) -> None:
        """Create the manager and mark tasks orphaned by a previous process.

        Args:
            workspace_root: Workspace directory; ``workspace/web`` by default.
            task_ttl_seconds: Workspace lifetime of finished tasks.
        """
        self.workspace_root = (
            Path(workspace_root) if workspace_root is not None else Path("workspace") / "web"
        )
        self.task_ttl_seconds = task_ttl_seconds
        self._tasks: Dict[str, TranslationTask] = {}
        self._lock = threading.Lock()
        #: IP -> task_id of the job occupying that IP's slot.
        self._active_by_ip: Dict[str, str] = {}
        #: task_id -> thread running that task's job.
        self._workers: Dict[str, threading.Thread] = {}
        #: Deleted tasks whose worker is still winding down.
        self._orphaned: Set[str] = set()
        #: task_id -> rebuild lock; an entry lives while a rebuild holds or awaits it.
        self._rebuild_locks: weakref.WeakValueDictionary[str, threading.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._reconcile_interrupted()

    def _reconcile_interrupted(self) -> None:
        """Flip tasks left unfinished by a dead worker to ``interrupted``.

        A process restart leaves DB rows in a non-terminal status with no live
        worker. They are marked ``interrupted`` (a terminal status) so clients
        stop seeing a forever-running job, and registered in memory so the TTL
        purge can later drop them.
        """
        for row in get_unfinished_task_rows():
            update_task_row(row["task_id"], status="interrupted")
            self._tasks[row["task_id"]] = TranslationTask.from_row({**row, "status": "interrupted"})

    def workspace_for_task(self, task_id: str) -> Path:
        """Return the task's workspace directory, creating it if needed.

        Args:
            task_id: Task UUID.

        Returns:
            The workspace directory.
        """
        path = self.workspace_root / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get(self, task_id: str) -> Optional[TranslationTask]:
        """Return the in-memory task.

        Args:
            task_id: Task UUID.

        Returns:
            The task, or ``None`` if it is not in memory.
        """
        with self._lock:
            return self._tasks.get(task_id)

    def find(self, task_id: str) -> Optional[TranslationTask]:
        """Return the task from memory, else rebuilt from its SQLite row.

        Tasks that finished under an earlier process exist only in the
        database; the ones it left unfinished are loaded as ``interrupted`` at
        startup.

        Args:
            task_id: Task UUID.

        Returns:
            The task, or ``None`` if it exists nowhere.
        """
        task = self.get(task_id)
        if task is not None:
            return task
        row = get_task_row(task_id)
        return TranslationTask.from_row(row) if row else None

    def active_task_count(self) -> int:
        """Return how many tasks have not reached a terminal status.

        Deleted tasks whose worker is still running count too. Exposed via
        ``/api/health`` so the deploy can wait for an idle service before
        recreating the container: a restart kills every worker thread, and there
        is no resume.
        """
        with self._lock:
            unfinished = sum(1 for t in self._tasks.values() if not t.is_finished())
            return unfinished + len(self._orphaned)

    def _slot_holder(self, ip: str) -> Optional[str]:
        """Return the unfinished task holding *ip*'s slot; the caller holds ``_lock``."""
        tid = self._active_by_ip.get(ip)
        task = self._tasks.get(tid) if tid else None
        return tid if task is not None and not task.is_finished() else None

    def active_task_id_for_ip(self, ip: str) -> Optional[str]:
        """Return the unfinished task occupying *ip*'s slot.

        Args:
            ip: Client IP address.

        Returns:
            The task id, or ``None`` when the slot is free.
        """
        with self._lock:
            return self._slot_holder(ip)

    def create_task(
        self,
        client_ip: str,
        input_filename: str,
        client_token: str = "",
        target_lang: Optional[str] = None,
        source_lang: Optional[str] = None,
        model: Optional[str] = None,
    ) -> TranslationTask:
        """Create a ``pending`` task in memory and in SQLite.

        Args:
            client_ip: Originating client IP address.
            input_filename: Original uploaded filename.
            client_token: Anonymous client UUID from localStorage.
            target_lang: Target translation language.
            source_lang: Source language.
            model: Model slug used for translation.

        Returns:
            The new task.
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

    def try_register_active(self, client_ip: str, task_id: str) -> bool:
        """Atomically register *task_id* for *client_ip* unless one is already active.

        The check and the registration happen in one critical section, so two
        concurrent requests from the same IP cannot both pass the one-job-per-IP
        limit.

        Args:
            client_ip: Client IP address.
            task_id: Task to register.

        Returns:
            ``True`` if registered; ``False`` if an unfinished task already
            occupies the slot for this IP.
        """
        with self._lock:
            if self._slot_holder(client_ip):
                return False
            self._active_by_ip[client_ip] = task_id
            return True

    def release_active(self, client_ip: str, task_id: str) -> None:
        """Free *client_ip*'s slot if *task_id* holds it.

        Args:
            client_ip: Client IP address.
            task_id: Task that may hold the slot.
        """
        with self._lock:
            if self._active_by_ip.get(client_ip) == task_id:
                del self._active_by_ip[client_ip]

    def cancel(self, task: TranslationTask) -> None:
        """Ask a running task to stop and free its client's slot at once.

        ``cancelling`` is persisted immediately so history and resume do not show
        a live job while the worker waits on an in-flight provider call; the
        worker still sets the final status. The slot is not held until then,
        because a hung provider call can take minutes to time out.

        Args:
            task: Running task.
        """
        task.request_cancel()
        task.status = "cancelling"
        update_task_row(task.task_id, status="cancelling")
        self.release_active(task.client_ip, task.task_id)

    def delete(self, task_id: str) -> None:
        """Delete a task with its workspace, database row and translations.

        A running job is cancelled and its client's slot freed at once. Its
        workspace goes when the worker exits, because the job may still be
        writing there; until then the worker counts as an active task. A task
        that never started (it lost the IP race or its upload failed) goes at
        once; without its row no TTL purge would reach its workspace.

        Args:
            task_id: Task to delete.
        """
        with self._lock:
            task = self._tasks.pop(task_id, None)
            worker_running = task_id in self._workers
            if worker_running:
                self._orphaned.add(task_id)
        if task is not None:
            task.request_cancel()
            self.release_active(task.client_ip, task_id)
        delete_task_row(task_id)
        if not worker_running:
            shutil.rmtree(self.workspace_root / task_id, ignore_errors=True)

    def rebuild(
        self, task: TranslationTask, edits: Sequence[RebuildEdit], target_lang: Optional[str]
    ) -> None:
        """Re-inject the task's translations plus *edits* and repack its module.

        No provider calls are made. An edit addresses one ``(file, item_id)`` and
        reaches every identical line its editor row stands for. The edits are
        persisted after a successful rebuild, so the editor and later rebuilds
        see the current values. Rebuilds of one task run one at a time: they
        patch the same extracted files and each starts from the edits the
        previous one stored.

        Args:
            task: A completed task whose ``extract_dir`` and ``result_path`` exist.
            edits: Edits from the editor.
            target_lang: Language that drives the string encoding.

        Raises:
            Exception: Whatever :func:`~nwn_translator.main.rebuild_module`
                raises, in which case no edit is stored, and database errors.
        """
        assert task.extract_dir is not None and task.result_path is not None
        with self._rebuild_lock(task.task_id):
            translations = get_item_translation_map_by_task(task.task_id)
            edited = editor.expand_edits(get_translations_by_task(task.task_id), edits)
            for (filename, item_id), text in edited.items():
                translations.setdefault(filename, {})[item_id] = text
            rebuild_module(
                task.extract_dir,
                translations,
                task.result_path,
                original_mod_path=task.input_path or task.result_path,
                target_lang=target_lang,
            )
            for (filename, item_id), text in edited.items():
                update_translation_text(task.task_id, filename, item_id, text)
            update_task_row(task.task_id, updated_at=time.time())

    @contextmanager
    def _rebuild_lock(self, task_id: str) -> Iterator[None]:
        """Hold the rebuild lock of *task_id*.

        The registry keeps the lock only weakly: each rebuild holding or awaiting
        it keeps it alive, and it disappears with the last one.

        Args:
            task_id: Task being rebuilt.

        Yields:
            Control while the lock is held.
        """
        with self._lock:
            lock = self._rebuild_locks.get(task_id)
            if lock is None:
                lock = self._rebuild_locks[task_id] = threading.Lock()
        with lock:
            yield

    def _make_progress_callback(self, task: TranslationTask) -> Callable[..., None]:
        """Create the pipeline progress callback of *task*.

        The callback updates the task's phase, status and monotonic weighted
        progress, and mirrors them into SQLite.

        Args:
            task: Task the callback reports for.

        Returns:
            The callback, called as ``(phase, current, total, message=None)``.
        """

        def callback(
            phase: str,
            current: int,
            total: int,
            message: Optional[str] = None,
        ) -> None:
            task.phase = phase
            # Do not clobber ``cancelling`` with a phase name: SQLite would look
            # "still translating" and the client would resume onto the progress
            # screen after a refresh.
            if not task.is_cancel_requested() and phase in _STATUS_PHASES:
                task.status = phase
            task.current_file = message

            start, end = _PHASE_WEIGHTS.get(phase, (0.0, 1.0))
            local = (current / total) if total else 0.0
            task.progress = max(task.progress, start + (end - start) * local)

            self._persist_progress(task, phase, message)

        return callback

    def _persist_progress(self, task: TranslationTask, phase: str, message: Optional[str]) -> None:
        """Mirror in-flight progress into SQLite.

        A phase change is written at once, other updates at most every
        :data:`PROGRESS_PERSIST_INTERVAL_SECONDS`. The history list reads the
        row's ``status``, so the row has to follow the running job instead of
        keeping its extraction-time state; status polls read the in-memory task.

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

    def start(self, task: TranslationTask, job: JobParams, input_path: Path) -> None:
        """Run the job of *task* on a worker thread of its own.

        Jobs run for minutes to hours, so they must not occupy asyncio's default
        executor, which the endpoints using ``asyncio.to_thread`` share.

        Args:
            task: Task that owns the job.
            job: Validated job settings.
            input_path: Uploaded module inside the task workspace.
        """
        worker = threading.Thread(
            target=self._run_job,
            args=(task, job, input_path),
            name=f"translate-{task.task_id}",
            daemon=True,
        )
        with self._lock:
            self._workers[task.task_id] = worker
        worker.start()

    def join_workers(self) -> None:
        """Wait until every running job has finished."""
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.join()

    def _run_job(self, task: TranslationTask, job: JobParams, input_path: Path) -> None:
        """Translate the uploaded module of *task* and record the outcome.

        The task ends ``completed``, ``cancelled`` or ``failed``, its IP slot is
        released and the worker is unregistered.

        Args:
            task: Task that owns the job.
            job: Validated job settings.
            input_path: Uploaded module inside the task workspace.
        """
        try:
            base = self.workspace_for_task(task.task_id)
            temp_dir = base / "temp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            task.input_path = input_path
            update_task_row(task.task_id, input_path=str(input_path))
            logger.info(
                "Task %s: target_lang=%r source_lang=%r module_encoding=%s",
                task.task_id,
                job.target_lang,
                job.source_lang,
                module_string_encoding_for_target_lang(job.target_lang),
            )
            task.status = "extracting"
            update_task_row(task.task_id, status="extracting")

            config = TranslationConfig(
                **asdict(job),
                input_file=input_path,
                output_file=create_output_path(input_path, job.target_lang, output_dir=base),
                translation_log=None,
                translation_log_writer=SqliteTranslationLogWriter(
                    task.task_id, trace_path=base / "translation_trace.jsonl"
                ),
                temp_dir=temp_dir,
                skip_cleanup=True,
                verbose=False,
                quiet=True,
                progress_callback=self._make_progress_callback(task),
                cancel_check=task.is_cancel_requested,
            )
            result_path, translator = run_translation_pipeline(config)
            task.result_path = Path(result_path)
            task.extract_dir = translator.extract_dir
            task.stats = translator.get_statistics()
            # Editor rows of every file, dialogs and rejected lines included;
            # ``items_translated`` counts only accepted non-dialog items.
            try:
                task.stats["texts_translated"] = count_translations(task.task_id)
            except sqlite3.Error as e:
                # The module is already written; only this statistic is lost.
                logger.warning("Could not count the rows of task %s: %s", task.task_id, e)
            self._finish(
                task,
                "completed",
                result_path=str(task.result_path),
                extract_dir=str(task.extract_dir),
                stats=task.stats,
            )
        except TranslationCancelled:
            logger.info("Translation cancelled for task %s", task.task_id)
            self._finish(task, "cancelled")
        except Exception as e:
            logger.exception("Translation failed for task %s", task.task_id)
            task.error = str(e)
            self._finish(task, "failed", error=str(e))
        finally:
            self.release_active(task.client_ip, task.task_id)
            with self._lock:
                del self._workers[task.task_id]
                deleted = task.task_id in self._orphaned
            if deleted:
                # The task stays active until its files are gone, so a deploy
                # waiting for zero active tasks cannot cut the removal short.
                shutil.rmtree(self.workspace_root / task.task_id, ignore_errors=True)
                with self._lock:
                    self._orphaned.discard(task.task_id)

    def _finish(self, task: TranslationTask, status: str, **fields: Any) -> None:
        """Move *task* to terminal *status* in memory and in SQLite.

        Args:
            task: Finished task.
            status: Terminal status.
            **fields: Extra columns to store with it.
        """
        task.progress = 1.0
        task.phase = None
        task.current_file = None
        task.status = status
        update_task_row(
            task.task_id,
            status=status,
            progress=1.0,
            phase=None,
            current_file=None,
            updated_at=time.time(),
            **fields,
        )

    def purge_expired(self) -> None:
        """Evict finished tasks older than the TTL from memory and disk.

        Workspace directories (uploaded module, extraction temp, result) are
        deleted; DB rows and translations are kept, so the client history and
        the translation editor keep working, while download answers 400 ("result
        file not ready") and rebuild 400 ("extracted files unavailable"). Expired
        tasks come from the DB, not the in-memory dict: finished tasks are not
        reloaded into memory after a restart, but their workspace files survive it.
        """
        now = time.time()
        with self._lock:
            to_delete: List[str] = [
                tid
                for tid, t in self._tasks.items()
                if now - t.created_at > self.task_ttl_seconds and t.is_finished()
            ]
            for tid in to_delete:
                self._tasks.pop(tid, None)

        for tid in get_finished_task_ids_older_than(now - self.task_ttl_seconds):
            # Task IDs are our own uuid4 strings; refuse anything that could
            # escape workspace_root in case a row was tampered with.
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

    async def purge_periodically(self, interval_seconds: float = PURGE_INTERVAL_SECONDS) -> None:
        """Run :meth:`purge_expired` every *interval_seconds* until cancelled.

        Args:
            interval_seconds: Pause between two purges.
        """
        while True:
            await asyncio.sleep(interval_seconds)
            self.purge_expired()


_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    """Return the process-wide :class:`TaskManager`, creating it on first use.

    The workspace root comes from ``NWN_WEB_TASK_ROOT``.
    """
    global _manager
    if _manager is None:
        root_env = os.environ.get("NWN_WEB_TASK_ROOT", "").strip()
        _manager = TaskManager(workspace_root=Path(root_env) if root_env else None)
    return _manager


def set_task_manager(manager: Optional[TaskManager]) -> None:
    """Replace the process-wide manager (tests); ``None`` resets it."""
    global _manager
    _manager = manager
