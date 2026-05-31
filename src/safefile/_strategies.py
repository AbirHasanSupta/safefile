import hashlib
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
    def __init__(self, chunk_size: int = 50 * 1024 * 1024, on_progress=None) -> None:
        self._chunk_size = chunk_size
        self._on_progress = on_progress

    def backup(self, src: str, dest: str) -> None:
        size = os.path.getsize(src)
        if self._on_progress and size > self._chunk_size:
            self._chunked_copy(src, dest, size)
        else:
            shutil.copy2(src, dest)

    def _chunked_copy(self, src: str, dest: str, size: int) -> None:
        """
        Streams src → dest in chunks, calling on_progress(pct) after each.
        Writes to a sibling .part file first; atomically renames to dest on
        completion so a mid-copy crash never leaves a half-written backup.
        """
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
            # ensure no partial backup is mistaken for a valid one
            if os.path.exists(part):
                os.remove(part)
            raise

    def restore(self, backup: str, original: str) -> None:
        os.replace(backup, original)

    def backup_dir(self, src: str, dest: str) -> None:
        shutil.copytree(src, dest)

    def restore_dir(self, backup: str, original: str) -> None:
        shutil.rmtree(original)
        shutil.copytree(backup, original)
        shutil.rmtree(backup)


class HardlinkStrategy(BackupStrategy):
    def __init__(self, chunk_size: int = 50 * 1024 * 1024, on_progress=None) -> None:
        self._chunk_size = chunk_size
        self._on_progress = on_progress

    def backup(self, src: str, dest: str) -> None:
        try:
            os.link(src, dest)
            if os.stat(src).st_ino != os.stat(dest).st_ino:
                raise OSError("inode mismatch after link")
            # shadow copy is the authoritative restore source for in-place writes
            shadow = dest + ".shadow"
            part = shadow + ".part"
            try:
                with open(src, "rb") as fsrc, open(part, "wb") as fdst:
                    for chunk in iter(lambda: fsrc.read(self._chunk_size), b""):
                        fdst.write(chunk)
                    fdst.flush()
                    try:
                        os.fsync(fdst.fileno())
                    except (OSError, AttributeError):
                        pass
                shutil.copystat(src, part)
                os.replace(part, shadow)
            except Exception:
                if os.path.exists(part):
                    os.remove(part)
                raise
        except OSError:
            # cross-device or unsupported: fall back to a plain copy
            if os.path.exists(dest):
                os.remove(dest)
            shutil.copy2(src, dest)

    def restore(self, backup: str, original: str) -> None:
        shadow = backup + ".shadow"
        if os.path.exists(shadow):
            os.replace(shadow, original)
            if os.path.exists(backup):
                os.remove(backup)
        else:
            os.replace(backup, original)

    def backup_dir(self, src: str, dest: str) -> None:
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


def get_strategy(
    name: str,
    chunk_size: int = 50 * 1024 * 1024,
    on_progress=None,
) -> BackupStrategy:
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy '{name}'. Choose from: {list(_STRATEGIES)}")
    return cls(chunk_size=chunk_size, on_progress=on_progress)