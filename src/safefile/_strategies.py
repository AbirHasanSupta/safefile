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


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
        written = 0
        with open(src, "rb") as fsrc, open(dest, "wb") as fdst:
            while True:
                chunk = fsrc.read(self._chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                written += len(chunk)
                self._on_progress(int(written * 100 / size))
        shutil.copystat(src, dest)

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
            shutil.copy2(src, dest + ".shadow")
        except OSError:
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


def get_strategy(name: str, chunk_size: int = 50 * 1024 * 1024, on_progress=None) -> BackupStrategy:
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy '{name}'. Choose from: {list(_STRATEGIES)}")
    return cls(chunk_size=chunk_size, on_progress=on_progress)