"""
pytest-safefile: auto-restore protected files after every test.

Usage (conftest.py or inline):

    import pytest

    @pytest.fixture
    def tx(safefile_fixture):
        safefile_fixture.protect("config.yaml", "data.csv")

Or use safefile_guard directly as a fixture argument:

    def test_something(safefile_guard):
        safefile_guard("config.yaml", "state.db")
        mutate_config()
        mutate_state()
        # both files restored automatically after the test, pass or fail
"""
from __future__ import annotations
from typing import Optional
import pytest
from ._transaction import Transaction


class SafefileGuard:
    """
    Holds one Transaction per test. Call .protect(*paths) at any point
    inside the test — even mid-test — to add files to protection.
    Everything is rolled back unconditionally at teardown.
    """

    def __init__(self, strategy: str = "copy", verify: bool = False) -> None:
        self._strategy = strategy
        self._verify = verify
        self._tx: Optional[Transaction] = None
        self._active = False

    def protect(self, *paths: str) -> None:
        if not self._active:
            raise RuntimeError(
                "safefile_guard.protect() called outside of a test. "
                "Make sure safefile_guard is used as a pytest fixture."
            )
        for p in paths:
            self._tx._register(p)

    def _start(self) -> None:
        self._tx = Transaction(
            strategy=self._strategy,
            verify=self._verify,
            journal=False,          # test-scoped; no need for crash recovery
        )
        import tempfile, os
        self._tx._temp_dir = tempfile.mkdtemp(prefix="safefile_pytest_")
        self._active = True

    def _stop(self) -> None:
        self._active = False
        if self._tx is not None:
            self._tx._rollback()
            self._tx = None


@pytest.fixture
def safefile_guard():
    """
    Fixture that gives the test a SafefileGuard.
    Call guard.protect(*paths) to snapshot files; they auto-restore after.
    """
    guard = SafefileGuard()
    guard._start()
    yield guard
    guard._stop()


@pytest.fixture
def safefile_guard_hardlink():
    """Same as safefile_guard but uses hardlink strategy for large files."""
    guard = SafefileGuard(strategy="hardlink")
    guard._start()
    yield guard
    guard._stop()


@pytest.fixture
def safefile_guard_verify():
    """Same as safefile_guard with checksum verification on restore."""
    guard = SafefileGuard(verify=True)
    guard._start()
    yield guard
    guard._stop()