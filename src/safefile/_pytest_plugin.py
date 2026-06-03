from __future__ import annotations

from typing import Optional, Generator
import pytest

from ._transaction import Transaction
from ._exceptions import SafefileError


class SafefileGuard:
    """
    Holds one Transaction per test. Call .protect(*paths) at any point
    inside the test — even mid-test — to add files to protection.
    Everything is rolled back unconditionally at teardown.

    The guard uses Transaction.__enter__ / __exit__ correctly so all
    Transaction invariants (journal, locks, hooks) are honoured.
    """

    def __init__(self, strategy: str = "copy", verify: bool = False) -> None:
        self._strategy = strategy
        self._verify = verify
        self._tx: Optional[Transaction] = None
        self._active = False

    def protect(self, *paths: str) -> None:
        if not self._active:
            raise RuntimeError(
                "safefile_guard.protect() called outside of an active test. "
                "Ensure safefile_guard is used as a pytest fixture argument."
            )
        if self._tx is None:
            raise RuntimeError("Transaction not initialized")
        for p in paths:
            self._tx._register(p)

    def _start(self) -> None:
        self._tx = Transaction(
            strategy=self._strategy,
            verify=self._verify,
            journal=False,  # test-scoped; crash recovery not needed
        )
        # Use the real __enter__ so temp_dir is created via mkdtemp and
        # all Transaction setup code runs exactly once, the normal way.
        self._tx.__enter__()
        self._active = True

    def _stop(self) -> None:
        self._active = False
        if self._tx is not None:
            # Simulate a failed block so __exit__ triggers rollback.
            # We suppress any RollbackError so teardown never masks a test failure.
            try:
                self._tx.__exit__(Exception, Exception("safefile teardown"), None)
            except SafefileError:
                pass
            self._tx = None


@pytest.fixture
def safefile_guard() -> Generator[SafefileGuard, None, None]:
    guard = SafefileGuard()
    guard._start()
    yield guard
    guard._stop()


@pytest.fixture
def safefile_guard_hardlink() -> Generator[SafefileGuard, None, None]:
    guard = SafefileGuard(strategy="hardlink")
    guard._start()
    yield guard
    guard._stop()


@pytest.fixture
def safefile_guard_verify() -> Generator[SafefileGuard, None, None]:
    guard = SafefileGuard(verify=True)
    guard._start()
    yield guard
    guard._stop()
