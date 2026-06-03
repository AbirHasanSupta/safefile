from typing import Callable, Dict, List, Optional, overload

class SafefileError(Exception): ...

class BackupError(SafefileError):
    path: str
    reason: str
    def __init__(self, path: str, reason: str) -> None: ...

class RestoreError(SafefileError):
    path: str
    reason: str
    def __init__(self, path: str, reason: str) -> None: ...

class RollbackError(SafefileError):
    errors: List[Exception]
    def __init__(self, errors: List[Exception]) -> None: ...

class ChecksumMismatchError(SafefileError):
    path: str
    expected: str
    got: str
    def __init__(self, path: str, expected: str, got: str) -> None: ...

class JournalError(SafefileError):
    journal_path: str
    reason: str
    def __init__(self, journal_path: str, reason: str) -> None: ...

class StrategyError(SafefileError): ...

class BackupStrategy:
    def backup(self, src: str, dest: str) -> None: ...
    def restore(self, backup: str, original: str) -> None: ...
    def backup_dir(self, src: str, dest: str) -> None: ...
    def restore_dir(self, backup: str, original: str) -> None: ...

class CopyStrategy(BackupStrategy):
    def __init__(
        self,
        chunk_size: int = ...,
        on_progress: Optional[Callable[[int], None]] = ...,
    ) -> None: ...

class HardlinkStrategy(BackupStrategy):
    def __init__(
        self,
        chunk_size: int = ...,
        on_progress: Optional[Callable[[int], None]] = ...,
    ) -> None: ...

def get_strategy(
    name: str,
    chunk_size: int = ...,
    on_progress: Optional[Callable[[int], None]] = ...,
) -> BackupStrategy: ...

class Savepoint:
    def restore(self) -> None: ...
    def discard(self) -> None: ...

class LazyWatcher:
    def touch(self, *paths: str) -> None: ...

class DryRunProxy:
    def path(self, original: str) -> str: ...
    def shadow_dir(self) -> str: ...
    def touch(self, *paths: str) -> None: ...
    def savepoint(self) -> object: ...
    def rollback_to(self, sp: object) -> None: ...
    def cleanup(self) -> None: ...

class Transaction:
    filepaths: "tuple[str, ...]"

    def __init__(
        self,
        *filepaths: str,
        strategy: str = ...,
        on_commit: Optional[Callable[[], None]] = ...,
        on_rollback: Optional[Callable[[], None]] = ...,
        lazy: bool = ...,
        dry_run: bool = ...,
        verify: bool = ...,
        journal: bool = ...,
        chunk_size: int = ...,
        on_progress: Optional[Callable[[int], None]] = ...,
    ) -> None: ...

    @overload
    def __enter__(self: "Transaction") -> "Transaction": ...
    @overload
    def __enter__(self: "Transaction") -> LazyWatcher: ...  # type: ignore[overload-cannot-match]
    @overload
    def __enter__(self: "Transaction") -> DryRunProxy: ...  # type: ignore[overload-cannot-match]

    def __exit__(
        self,
        exc_type: object,
        exc_val: object,
        exc_tb: object,
    ) -> bool: ...

    def savepoint(self) -> Savepoint: ...
    def rollback_to(self, sp: Savepoint) -> None: ...

class AsyncTransaction:
    def __init__(self, *filepaths: str, **kwargs: object) -> None: ...
    async def __aenter__(self) -> Transaction: ...
    async def __aexit__(
        self,
        exc_type: object,
        exc_val: object,
        exc_tb: object,
    ) -> bool: ...

def recover_orphaned(verbose: bool = ...) -> int: ...
def find_orphaned_journals() -> List[Dict[str, object]]: ...

class SafefileGuard:
    def __init__(self, strategy: str = ..., verify: bool = ...) -> None: ...
    def protect(self, *paths: str) -> None: ...

def transaction(
    *filepaths: str,
    strategy: str = ...,
    on_commit: Optional[Callable[[], None]] = ...,
    on_rollback: Optional[Callable[[], None]] = ...,
    lazy: bool = ...,
    dry_run: bool = ...,
    verify: bool = ...,
    journal: bool = ...,
    chunk_size: int = ...,
    on_progress: Optional[Callable[[int], None]] = ...,
) -> Transaction: ...

def async_transaction(
    *filepaths: str,
    strategy: str = ...,
    on_commit: Optional[Callable[[], None]] = ...,
    on_rollback: Optional[Callable[[], None]] = ...,
    lazy: bool = ...,
    dry_run: bool = ...,
    verify: bool = ...,
    journal: bool = ...,
    chunk_size: int = ...,
    on_progress: Optional[Callable[[int], None]] = ...,
) -> AsyncTransaction: ...
