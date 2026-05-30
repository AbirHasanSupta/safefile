import os
from typing import Callable, Set


class LazyWatcher:
    """
    Returned from Transaction.__enter__ when lazy=True.
    Call tx.touch(path) before modifying a file to trigger its backup on demand.
    Files never touch()'d are never backed up.
    """

    def __init__(self, register: Callable[[str], None]) -> None:
        self._register = register
        self._touched: Set[str] = set()

    def touch(self, *paths: str) -> None:
        for p in paths:
            if p not in self._touched:
                self._touched.add(p)
                self._register(p)