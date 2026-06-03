import asyncio
import hashlib
import os
import shutil
import tempfile
import threading
from types import TracebackType
from typing import Callable, Dict, List, Optional, Set, Union, Type, Literal, Any, cast

from ._strategies import BackupStrategy, get_strategy
from ._savepoint import Savepoint
from ._lazy import LazyWatcher
from ._dryrun import DryRunProxy
from ._journal import write_journal, mark_committed
from ._exceptions import (
    BackupError,
    ChecksumMismatchError,
    RestoreError,
    RollbackError,
)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Transaction:
    def __init__(
        self,
        *filepaths: str,
        strategy: str = "copy",
        on_commit: Optional[Callable[[], None]] = None,
        on_rollback: Optional[Callable[[], None]] = None,
        lazy: bool = False,
        dry_run: bool = False,
        verify: bool = False,
        journal: bool = True,
        chunk_size: int = 50 * 1024 * 1024,
        on_progress: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.filepaths = filepaths
        self._strategy_name = strategy
        self._strategy: BackupStrategy = get_strategy(
            strategy, chunk_size=chunk_size, on_progress=on_progress
        )
        self._on_commit = on_commit
        self._on_rollback = on_rollback
        self._lazy = lazy
        self._dry_run = dry_run
        self._verify = verify
        self._journal = journal
        self._backups: Dict[str, str] = {}
        self._checksums: Dict[str, str] = {}
        self._new_paths: Set[str] = set()
        self._temp_dir: Optional[str] = None
        self._dirs: Set[str] = set()
        self._savepoints: List[Savepoint] = []
        self._sp_counter: int = 0
        self._dry_proxy: Optional[DryRunProxy] = None
        # thread safety: all mutations to shared state go through this lock
        self._lock = threading.RLock()

    # ── enter ─────────────────────────────────────────────────────────────────

    def __enter__(self) -> Union["Transaction", LazyWatcher, DryRunProxy]:
        if self._dry_run:
            self._dry_proxy = DryRunProxy(self.filepaths, self._strategy)
            return self._dry_proxy

        self._temp_dir = tempfile.mkdtemp(prefix="safefile_")

        if self._lazy:
            return LazyWatcher(self._register)

        for fp in self.filepaths:
            self._register(fp)

        if self._journal and self._backups:
            self._write_journal()

        return self

    def _register(self, fp: str) -> None:
        with self._lock:
            if fp in self._backups or fp in self._new_paths:
                return
            if self._temp_dir is None:
                raise RuntimeError("Transaction not entered")
            if os.path.isdir(fp):
                self._dirs.add(fp)
                backup_path = os.path.join(
                    self._temp_dir, os.path.basename(fp.rstrip(os.sep)) + ".dirbak"
                )
                self._strategy.backup_dir(fp, backup_path)
                self._backups[fp] = backup_path
            elif os.path.exists(fp):
                backup_path = os.path.join(
                    self._temp_dir, os.path.basename(fp) + ".bak"
                )
                self._strategy.backup(fp, backup_path)
                if self._verify:
                    self._checksums[fp] = _sha256(fp)
                self._backups[fp] = backup_path
            else:
                self._new_paths.add(fp)

            if self._journal and self._temp_dir and not self._dry_run:
                self._write_journal()

    def _write_journal(self) -> None:
        if self._temp_dir is None:
            raise RuntimeError("Transaction not entered")
        write_journal(
            temp_dir=self._temp_dir,
            filepaths=list(self.filepaths),
            backups=self._backups,
            new_paths=self._new_paths,
            dirs=self._dirs,
            strategy=self._strategy_name,
        )

    # ── exit ──────────────────────────────────────────────────────────────────

    def __exit__(self,
                 exc_type: Optional[Type[BaseException]],
                 exc_val: Optional[BaseException],
                 exc_tb: Optional[TracebackType]) -> Literal[False]:
        if self._dry_run:
            if self._dry_proxy:
                self._dry_proxy.cleanup()
            return False

        with self._lock:
            for sp in self._savepoints:
                sp.discard()
            self._savepoints.clear()

        if exc_type is None:
            if self._journal and self._temp_dir:
                mark_committed(self._temp_dir)
            self._cleanup_temp()
            if self._on_commit:
                self._on_commit()
        else:
            self._rollback()
            if self._on_rollback:
                self._on_rollback()
        return False

    # ── savepoints ────────────────────────────────────────────────────────────

    def savepoint(self) -> Savepoint:
        with self._lock:
            sp = Savepoint(
                self._strategy,
                self._backups,
                self._dirs,
                self._new_paths,
                self._sp_counter,
            )
            self._sp_counter += 1
            self._savepoints.append(sp)
            return sp

    def rollback_to(self, sp: Savepoint) -> None:
        with self._lock:
            idx = self._savepoints.index(sp)
            for later_sp in self._savepoints[idx + 1:]:
                later_sp.discard()
            self._savepoints = self._savepoints[:idx]

            sp.restore()

            # Undo registrations that happened AFTER this savepoint was taken.
            #
            # Case A – files backed up post-savepoint (existed at _register time
            #          but were unknown at savepoint time): delete from disk and
            #          discard the orphan backup sitting in our temp dir.
            post_backed = set(self._backups.keys()) - set(sp._backups.keys())
            for fp in post_backed:
                backup_path = self._backups.get(fp)
                if backup_path:
                    try:
                        if os.path.isdir(backup_path):
                            shutil.rmtree(backup_path, ignore_errors=True)
                        elif os.path.exists(backup_path):
                            os.remove(backup_path)
                    except OSError:
                        pass
                if os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                elif os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass

            # Case B – paths registered as new (didn't exist) post-savepoint:
            #          delete if they were subsequently created on disk.
            post_new = self._new_paths - sp._new_paths
            for fp in post_new:
                if os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                elif os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass

            # Synchronise tracking state back to the savepoint snapshot
            self._backups = {k: v for k, v in self._backups.items() if k in sp._backups}
            self._new_paths = set(sp._new_paths)
            self._dirs = set(sp._dirs)
            if self._verify:
                self._checksums = {
                    k: v for k, v in self._checksums.items() if k in sp._backups
                }

            if self._journal and self._temp_dir:
                self._write_journal()

    # ── rollback ──────────────────────────────────────────────────────────────

    def _rollback(self) -> None:
        errors: List[Exception] = []
        with self._lock:
            backups_snapshot = dict(self._backups)
            new_paths_snapshot = set(self._new_paths)
            dirs_snapshot = set(self._dirs)
            checksums_snapshot = dict(self._checksums)

        for original, backup in backups_snapshot.items():
            try:
                if original in dirs_snapshot:
                    self._strategy.restore_dir(backup, original)
                else:
                    self._strategy.restore(backup, original)
                    if self._verify and original in checksums_snapshot:
                        restored = _sha256(original)
                        expected = checksums_snapshot[original]
                        if restored != expected:
                            errors.append(
                                ChecksumMismatchError(original, expected, restored)
                            )
            except (RestoreError, Exception) as e:
                errors.append(e)

        for fp in new_paths_snapshot:
            try:
                if os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                elif os.path.exists(fp):
                    os.remove(fp)
            except Exception as e:
                errors.append(e)

        self._cleanup_temp()

        if errors:
            raise RollbackError(errors)

    def _cleanup_temp(self) -> None:
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir)


# ── async wrapper ─────────────────────────────────────────────────────────────

class AsyncTransaction:
    def __init__(self, *filepaths: str, **kwargs: Any) -> None:
        self._tx = Transaction(*filepaths, **kwargs)

    async def __aenter__(self) -> Transaction:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            cast(Callable[[], Transaction], self._tx.__enter__)
        )

    async def __aexit__(self,
                        exc_type: Optional[Type[BaseException]],
                        exc_val: Optional[BaseException],
                        exc_tb: Optional[TracebackType]) -> bool:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: self._tx.__exit__(exc_type, exc_val, exc_tb)
        )
        return False
