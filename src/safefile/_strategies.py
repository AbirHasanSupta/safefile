import errno
import hashlib
import os
import shutil
from abc import ABC, abstractmethod

from ._exceptions import BackupError, RestoreError, StrategyError


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
    def __init__(self, chunk_size: int = 50 * 1024 * 1024, on_progress=None) -> None:
        self._chunk_size = chunk_size
        self._on_progress = on_progress

    def backup(self, src: str, dest: str) -> None:
        try:
            size = os.path.getsize(src)
            if self._on_progress and size > self._chunk_size:
                self._chunked_copy(src, dest, size)
            else:
                shutil.copy2(src, dest)
        except OSError as exc:
            raise BackupError(src, str(exc)) from exc

    def _chunked_copy(self, src: str, dest: str, size: int) -> None:
        part = dest + ".part"
        written = 0
        try:
            with open(src, "rb") as fsrc, open(part, "wb") as fdst:
                while True:
                    chunk = fsrc.read(self._chunk_size)
                    if not chunk:
                        break
                    fdst.write(chunk)
                    written += len(chunk)
                    self._on_progress(int(written * 100 / size))
                fdst.flush()
                try:
                    os.fsync(fdst.fileno())
                except (OSError, AttributeError):
                    pass
            shutil.copystat(src, part)
            os.replace(part, dest)
        except Exception:
            if os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
            raise

    def restore(self, backup: str, original: str) -> None:
        try:
            os.replace(backup, original)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                # backup and original are on different filesystems
                try:
                    shutil.copy2(backup, original)
                    os.remove(backup)
                except OSError as exc2:
                    raise RestoreError(original, str(exc2)) from exc2
            else:
                raise RestoreError(original, str(exc)) from exc

    def backup_dir(self, src: str, dest: str) -> None:
        try:
            shutil.copytree(src, dest)
        except OSError as exc:
            raise BackupError(src, str(exc)) from exc

    def restore_dir(self, backup: str, original: str) -> None:
        try:
            if os.path.exists(original):
                shutil.rmtree(original)
            shutil.copytree(backup, original)
            shutil.rmtree(backup)
        except OSError as exc:
            raise RestoreError(original, str(exc)) from exc


class HardlinkStrategy(BackupStrategy):
    """
    Backup strategy that creates a hard link plus a shadow copy.

    Why both?
    ---------
    A hard link shares the same inode as the source. If the caller later
    *replaces* the file atomically (os.replace / write-to-temp-then-rename)
    the original inode survives untouched in the backup link — restore is a
    free inode-rename via os.replace.

    If the caller *modifies the file in-place* (open(path, "w") truncate-
    and-write, database writes, mmap flushes) the shared inode is overwritten
    and the hardlink reflects the change too.  The shadow copy is the
    fallback for exactly this case — it always holds the original content
    regardless of how the original was modified.

    At restore time the strategy checks whether the inode changed:
    - different inode → atomic replace occurred → use the hardlink (fast)
    - same inode     → in-place write occurred  → use the shadow copy

    The shadow copy path is therefore not redundant: it is only consumed
    when the hardlink has been corrupted by an in-place write.  For the
    common case of atomically-replaced files (editors, deploy scripts) the
    shadow copy is discarded unused and the restore is a single O(1) rename.

    Falls back to a plain copy when os.link() is unsupported (cross-device,
    FAT32, some network filesystems).  In that case no shadow is written
    because the plain copy already contains the original content.
    """

    def __init__(self, chunk_size: int = 50 * 1024 * 1024, on_progress=None) -> None:
        self._chunk_size = chunk_size
        self._on_progress = on_progress

    def backup(self, src: str, dest: str) -> None:
        try:
            os.link(src, dest)
        except OSError:
            # Cross-device or unsupported filesystem: plain copy (no shadow needed).
            try:
                shutil.copy2(src, dest)
            except OSError as exc:
                raise BackupError(src, str(exc)) from exc
            return

        # Hardlink succeeded: write shadow copy for in-place-write protection.
        shadow = dest + ".shadow"
        try:
            size = os.path.getsize(src)
            if self._on_progress and size > self._chunk_size:
                self._chunked_shadow(src, shadow, size)
            else:
                shutil.copy2(src, shadow)
        except OSError as exc:
            # Shadow creation failed — clean up the dangling hardlink and
            # re-raise so the caller falls back to CopyStrategy if desired.
            try:
                os.remove(dest)
            except OSError:
                pass
            raise BackupError(src, str(exc)) from exc

    def _chunked_shadow(self, src: str, shadow: str, size: int) -> None:
        part = shadow + ".part"
        written = 0
        try:
            with open(src, "rb") as fsrc, open(part, "wb") as fdst:
                for chunk in iter(lambda: fsrc.read(self._chunk_size), b""):
                    fdst.write(chunk)
                    written += len(chunk)
                    self._on_progress(int(written * 100 / size))
                fdst.flush()
                try:
                    os.fsync(fdst.fileno())
                except (OSError, AttributeError):
                    pass
            shutil.copystat(src, part)
            os.replace(part, shadow)
        except Exception:
            if os.path.exists(part):
                try:
                    os.remove(part)
                except OSError:
                    pass
            raise

    def restore(self, backup: str, original: str) -> None:
        shadow = backup + ".shadow"
        try:
            if os.path.exists(shadow):
                # Hardlink strategy was used.  Decide which copy to restore from.
                if os.path.exists(original) and os.path.exists(backup):
                    try:
                        orig_ino = os.stat(original).st_ino
                        bak_ino = os.stat(backup).st_ino
                        if orig_ino != bak_ino:
                            # File was atomically replaced: the hardlink still
                            # holds the original inode — restore is a free rename.
                            os.replace(backup, original)
                            try:
                                os.remove(shadow)
                            except OSError:
                                pass
                            return
                    except OSError:
                        pass
                # In-place write (same inode) or original is gone: use shadow.
                os.replace(shadow, original)
                try:
                    os.remove(backup)
                except OSError:
                    pass
            else:
                # Cross-device fallback: backup is a plain copy.
                self._replace_with_exdev_fallback(backup, original)
        except OSError as exc:
            raise RestoreError(original, str(exc)) from exc

    @staticmethod
    def _replace_with_exdev_fallback(src: str, dest: str) -> None:
        try:
            os.replace(src, dest)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                shutil.copy2(src, dest)
                os.remove(src)
            else:
                raise

    def backup_dir(self, src: str, dest: str) -> None:
        # Directory backup always uses plain copies. Hardlinks cannot be used
        # here because restore_dir does a bulk copytree with no per-file inode
        # check, so an in-place write would silently corrupt the backup.
        try:
            shutil.copytree(src, dest)
        except OSError as exc:
            raise BackupError(src, str(exc)) from exc

    def restore_dir(self, backup: str, original: str) -> None:
        try:
            if os.path.exists(original):
                shutil.rmtree(original)
            shutil.copytree(backup, original)
            shutil.rmtree(backup)
        except OSError as exc:
            raise RestoreError(original, str(exc)) from exc


_STRATEGIES: dict = {
    "copy": CopyStrategy,
    "hardlink": HardlinkStrategy,
}


def get_strategy(
    name: str,
    chunk_size: int = 50 * 1024 * 1024,
    on_progress=None,
) -> BackupStrategy:
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise StrategyError(
            f"Unknown strategy '{name}'. Choose from: {list(_STRATEGIES)}"
        )
    return cls(chunk_size=chunk_size, on_progress=on_progress)
