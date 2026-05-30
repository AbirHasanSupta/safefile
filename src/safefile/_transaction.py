import asyncio
import hashlib
import os
import shutil
import tempfile
from typing import Callable, Dict, Iterator, List, Optional, Set, Union

from ._strategies import BackupStrategy, get_strategy
from ._savepoint import Savepoint
from ._lazy import LazyWatcher
from ._dryrun import DryRunProxy


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
        chunk_size: int = 50 * 1024 * 1024,
        on_progress: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.filepaths = filepaths
        self._strategy: BackupStrategy = get_strategy(
            strategy, chunk_size=chunk_size, on_progress=on_progress
        )
        self._on_commit = on_commit
        self._on_rollback = on_rollback
        self._lazy = lazy
        self._dry_run = dry_run
        self._verify = verify
        self._backups: Dict[str, str] = {}
        self._checksums: Dict[str, str] = {}
        self._new_paths: Set[str] = set()
        self._temp_dir: Optional[str] = None
        self._dirs: Set[str] = set()
        self._savepoints: List[Savepoint] = []
        self._sp_counter: int = 0
        self._dry_proxy: Optional[DryRunProxy] = None

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

        return self

    def _register(self, fp: str) -> None:
        """Back up a single path (file or dir). Safe to call multiple times."""
        if fp in self._backups or fp in self._new_paths:
            return
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

    # ── exit ──────────────────────────────────────────────────────────────────

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._dry_run:
            if self._dry_proxy:
                self._dry_proxy.cleanup()
            return False

        for sp in self._savepoints:
            sp.discard()
        self._savepoints.clear()

        if exc_type is None:
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
        """Snapshot current file states mid-transaction. Returns a Savepoint."""
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
        """
        Restore files to the state they were in when sp was created.
        Savepoints created after sp are discarded.
        """
        idx = self._savepoints.index(sp)
        for later_sp in self._savepoints[idx + 1:]:
            later_sp.discard()
        self._savepoints = self._savepoints[:idx]
        sp.restore()

    # ── rollback ──────────────────────────────────────────────────────────────

    def _rollback(self) -> None:
        for original, backup in self._backups.items():
            if original in self._dirs:
                self._strategy.restore_dir(backup, original)
            else:
                self._strategy.restore(backup, original)
                if self._verify and original in self._checksums:
                    restored = _sha256(original)
                    expected = self._checksums[original]
                    if restored != expected:
                        raise RuntimeError(
                            f"Checksum mismatch after restoring '{original}': "
                            f"expected {expected}, got {restored}"
                        )
        for fp in self._new_paths:
            if os.path.isdir(fp):
                shutil.rmtree(fp, ignore_errors=True)
            elif os.path.exists(fp):
                os.remove(fp)
        self._cleanup_temp()

    def _cleanup_temp(self) -> None:
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir)


# ── async wrapper ─────────────────────────────────────────────────────────────

class AsyncTransaction:
    """
    Async context manager. Backup/restore run in a thread pool to avoid
    blocking the event loop. Zero extra dependencies (uses asyncio.to_thread).
    """

    def __init__(self, *filepaths: str, **kwargs) -> None:
        self._tx = Transaction(*filepaths, **kwargs)

    async def __aenter__(self) -> Transaction:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._tx.__enter__)
        return result

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: self._tx.__exit__(exc_type, exc_val, exc_tb)
        )
        return False