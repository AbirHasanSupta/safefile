import os
import shutil
import tempfile
from typing import Dict, Optional, Set

class Transaction:
    """
    Context manager that protects files from being left in an inconsistent
    state if an exception occurs during modification.

    Usage:
        with Transaction("config.yaml", "data.csv"):
            # modify the files freely
    """

    def __init__(self, *filepaths: str) -> None:
        self.filepaths = filepaths
        self._backups: Dict[str, str] = {}   # original_path -> backup_path
        self._temp_dir: Optional[str] = None
        self._new_files: Set[str] = set()

    def __enter__(self) -> "Transaction":
        self._temp_dir = tempfile.mkdtemp(prefix="safefile_")
        for fp in self.filepaths:
            if os.path.exists(fp):
                # Create a safe copy in a temporary directory
                backup_path = os.path.join(
                    self._temp_dir, os.path.basename(fp) + ".bak"
                )
                shutil.copy2(fp, backup_path)   # preserves metadata
                self._backups[fp] = backup_path
            else:
                # Track files that did not exist (will be deleted on rollback)
                self._new_files.add(fp)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            # Success: discard backups, keep modified files
            if self._temp_dir and os.path.isdir(self._temp_dir):
                shutil.rmtree(self._temp_dir)
        else:
            # Failure: restore originals from backups
            for original, backup in self._backups.items():
                os.replace(backup, original)
            for fp in self._new_files:
                if os.path.exists(fp):
                    os.remove(fp)
            if self._temp_dir and os.path.isdir(self._temp_dir):
                shutil.rmtree(self._temp_dir)
        return False   # propagate exception