from ._transaction import Transaction, AsyncTransaction
from ._strategies import get_strategy, CopyStrategy, HardlinkStrategy
from ._savepoint import Savepoint
from ._lazy import LazyWatcher
from ._dryrun import DryRunProxy
from ._journal import recover_orphaned, find_orphaned_journals
from ._pytest_plugin import SafefileGuard

__version__ = "1.0.0"
__all__ = [
    "transaction",
    "async_transaction",
    "recover_orphaned",
    "find_orphaned_journals",
    "Transaction",
    "AsyncTransaction",
    "Savepoint",
    "LazyWatcher",
    "DryRunProxy",
    "SafefileGuard",
    "get_strategy",
    "CopyStrategy",
    "HardlinkStrategy",
]


def transaction(
    *filepaths: str,
    strategy: str = "copy",
    on_commit=None,
    on_rollback=None,
    lazy: bool = False,
    dry_run: bool = False,
    verify: bool = False,
    journal: bool = True,
    chunk_size: int = 50 * 1024 * 1024,
    on_progress=None,
) -> Transaction:
    return Transaction(
        *filepaths,
        strategy=strategy,
        on_commit=on_commit,
        on_rollback=on_rollback,
        lazy=lazy,
        dry_run=dry_run,
        verify=verify,
        journal=journal,
        chunk_size=chunk_size,
        on_progress=on_progress,
    )


def async_transaction(
    *filepaths: str,
    strategy: str = "copy",
    on_commit=None,
    on_rollback=None,
    lazy: bool = False,
    dry_run: bool = False,
    verify: bool = False,
    journal: bool = True,
    chunk_size: int = 50 * 1024 * 1024,
    on_progress=None,
) -> AsyncTransaction:
    return AsyncTransaction(
        *filepaths,
        strategy=strategy,
        on_commit=on_commit,
        on_rollback=on_rollback,
        lazy=lazy,
        dry_run=dry_run,
        verify=verify,
        journal=journal,
        chunk_size=chunk_size,
        on_progress=on_progress,
    )