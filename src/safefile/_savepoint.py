import os
import shutil
import tempfile
from typing import Dict, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from ._strategies import BackupStrategy


class Savepoint:
    """
    A mid-transaction snapshot. Created via tx.savepoint().
    Roll back to this point with tx.rollback_to(sp).
    Files modified after the savepoint revert; files from before it stay.
    """

    def __init__(
        self,
        strategy: "BackupStrategy",
        backups: Dict[str, str],
        dirs: Set[str],
        new_paths: Set[str],
        sp_index: int,
    ) -> None:
        self._strategy = strategy
        self._sp_dir = tempfile.mkdtemp(prefix=f"safefile_sp{sp_index}_")
        # snapshot current file states into savepoint dir
        self._backups: Dict[str, str] = {}
        self._dirs = set(dirs)
        self._new_paths = set(new_paths)

        for original, _ in backups.items():
            if original in dirs:
                sp_backup = os.path.join(
                    self._sp_dir,
                    os.path.basename(original.rstrip(os.sep)) + ".dirbak",
                )
                self._strategy.backup_dir(original, sp_backup)
            else:
                sp_backup = os.path.join(
                    self._sp_dir, os.path.basename(original) + ".bak"
                )
                self._strategy.backup(original, sp_backup)
            self._backups[original] = sp_backup

    def restore(self) -> None:
        for original, backup in self._backups.items():
            if original in self._dirs:
                self._strategy.restore_dir(backup, original)
                # restore_dir deletes backup, so don't try to clean it
            else:
                self._strategy.restore(backup, original)
        # remove files that did not exist at savepoint time
        for fp in self._new_paths:
            if os.path.isdir(fp):
                shutil.rmtree(fp, ignore_errors=True)
            elif os.path.exists(fp):
                os.remove(fp)
        self._cleanup()

    def discard(self) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        if os.path.isdir(self._sp_dir):
            shutil.rmtree(self._sp_dir)