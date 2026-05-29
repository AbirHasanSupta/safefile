from ._transaction import Transaction
from ._strategies import get_strategy, CopyStrategy, HardlinkStrategy

__version__ = "0.2.0"
__all__ = ["transaction", "Transaction", "get_strategy", "CopyStrategy", "HardlinkStrategy"]


def transaction(
    *filepaths: str,
    strategy: str = "copy",
    on_commit=None,
    on_rollback=None,
) -> Transaction:
    return Transaction(
        *filepaths,
        strategy=strategy,
        on_commit=on_commit,
        on_rollback=on_rollback,
    )