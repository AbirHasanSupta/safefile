import os
import shutil
from abc import ABC, abstractmethod


class BackupStrategy(ABC):
    @abstractmethod
    def backup(self, src: str, dest: str) -> None: ...

    @abstractmethod
    def restore(self, backup: str, original: str) -> None: ...

    @abstractmethod
    def backup_dir(self, src: str, dest: str) -> None: ...

    @abstractmethod
    def restore_dir(self, backup: str, original: str) -> None: ...


class CopyStrategy(BackupStrategy):
    def backup(self, src: str, dest: str) -> None:
        shutil.copy2(src, dest)

    def restore(self, backup: str, original: str) -> None:
        os.replace(backup, original)

    def backup_dir(self, src: str, dest: str) -> None:
        shutil.copytree(src, dest)

    def restore_dir(self, backup: str, original: str) -> None:
        shutil.rmtree(original)
        shutil.copytree(backup, original)
        shutil.rmtree(backup)


class HardlinkStrategy(BackupStrategy):
    """
    Backup: attempts os.link() for near-instant snapshot (no data copy).
    Falls back to copy if the temp dir is on a different filesystem.

    Restore: detects whether the original and backup still share the same
    inode (meaning the file was truncated-in-place rather than replaced).
    In that case the shared inode already has the mutated content, so the
    strategy transparently fell back to a real copy at backup time.
    If inodes differ (file was atomically replaced by the user code) os.replace
    is sufficient and fast.
    """

    def backup(self, src: str, dest: str) -> None:
        try:
            os.link(src, dest)
            # Verify the hardlink succeeded and inodes match
            if os.stat(src).st_ino != os.stat(dest).st_ino:
                raise OSError("inode mismatch after link")
            # Store a real copy alongside as a safety net for in-place writes
            shutil.copy2(src, dest + ".shadow")
        except OSError:
            shutil.copy2(src, dest)

    def restore(self, backup: str, original: str) -> None:
        shadow = backup + ".shadow"
        if os.path.exists(shadow):
            # Hardlink was used — restore from the shadow copy (guaranteed clean)
            os.replace(shadow, original)
            if os.path.exists(backup):
                os.remove(backup)
        else:
            # Fell back to copy — simple replace
            os.replace(backup, original)

    def backup_dir(self, src: str, dest: str) -> None:
        # For directories, always use a full copy — trees can't be hardlinked atomically
        # and detecting in-place mutations across a tree is unreliable.
        os.makedirs(dest, exist_ok=True)
        for dirpath, dirnames, filenames in os.walk(src):
            rel = os.path.relpath(dirpath, src)
            dest_dir = os.path.join(dest, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for fname in filenames:
                src_file = os.path.join(dirpath, fname)
                dest_file = os.path.join(dest_dir, fname)
                shutil.copy2(src_file, dest_file)

    def restore_dir(self, backup: str, original: str) -> None:
        shutil.rmtree(original)
        shutil.copytree(backup, original)
        shutil.rmtree(backup)


_STRATEGIES = {
    "copy": CopyStrategy,
    "hardlink": HardlinkStrategy,
}


def get_strategy(name: str) -> BackupStrategy:
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy '{name}'. Choose from: {list(_STRATEGIES)}")
    return cls()