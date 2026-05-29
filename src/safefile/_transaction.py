import os
import shutil
import tempfile
from typing import Callable, Dict, Optional, Set

from ._strategies import BackupStrategy, get_strategy


class Transaction:
    def __init__(
        self,
        *filepaths: str,
        strategy: str = "copy",
        on_commit: Optional[Callable[[], None]] = None,
        on_rollback: Optional[Callable[[], None]] = None,
    ) -> None:
        self.filepaths = filepaths
        self._strategy: BackupStrategy = get_strategy(strategy)
        self._on_commit = on_commit
        self._on_rollback = on_rollback
        self._backups: Dict[str, str] = {}
        self._new_paths: Set[str] = set()
        self._temp_dir: Optional[str] = None
        self._dirs: Set[str] = set()

    def __enter__(self) -> "Transaction":
        self._temp_dir = tempfile.mkdtemp(prefix="safefile_")
        for fp in self.filepaths:
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
                self._backups[fp] = backup_path
            else:
                self._new_paths.add(fp)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            self._cleanup_temp()
            if self._on_commit:
                self._on_commit()
        else:
            self._rollback()
            if self._on_rollback:
                self._on_rollback()
        return False

    def _rollback(self) -> None:
        for original, backup in self._backups.items():
            if original in self._dirs:
                self._strategy.restore_dir(backup, original)
            else:
                self._strategy.restore(backup, original)
        for fp in self._new_paths:
            if os.path.isdir(fp):
                shutil.rmtree(fp, ignore_errors=True)
            elif os.path.exists(fp):
                os.remove(fp)
        self._cleanup_temp()

    def _cleanup_temp(self) -> None:
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir)