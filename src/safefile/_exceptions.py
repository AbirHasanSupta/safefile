from typing import List


class SafefileError(Exception):
    """Base exception for all safefile errors."""
    pass


class BackupError(SafefileError):
    """Raised when a backup operation fails."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Backup failed for '{path}': {reason}")


class RestoreError(SafefileError):
    """Raised when a single file restore fails during rollback."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Restore failed for '{path}': {reason}")


class RollbackError(SafefileError):
    """
    Raised when rollback completes but one or more files could not be restored.
    All restorable files are still restored before this is raised.
    """

    def __init__(self, errors: List[Exception]) -> None:
        self.errors: List[Exception] = errors
        summary = "; ".join(str(e) for e in errors)
        super().__init__(
            f"Rollback completed with {len(errors)} error(s): {summary}"
        )


class ChecksumMismatchError(SafefileError):
    """Raised when a restored file's checksum does not match the original backup."""

    def __init__(self, path: str, expected: str, got: str) -> None:
        self.path = path
        self.expected = expected
        self.got = got
        super().__init__(
            f"Checksum mismatch after restoring '{path}': "
            f"expected {expected}, got {got}"
        )


class JournalError(SafefileError):
    """Raised when reading or writing the crash-recovery journal fails."""

    def __init__(self, journal_path: str, reason: str) -> None:
        self.journal_path = journal_path
        self.reason = reason
        super().__init__(f"Journal error at '{journal_path}': {reason}")


class StrategyError(SafefileError):
    """Raised when an unknown or invalid backup strategy is requested."""
    pass