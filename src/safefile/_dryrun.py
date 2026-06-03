import os
import shutil
import tempfile
from typing import Dict


class DryRunProxy:
    """
    Returned from Transaction.__enter__ when dry_run=True.
    Intercepts writes by redirecting paths to shadow copies.
    The original files are never modified.

    Exposes touch(), savepoint(), rollback_to() as no-ops so that
    combining dry_run=True with lazy=True or savepoint calls does not
    raise AttributeError.

    Usage:
        with transaction("prod.cfg", dry_run=True) as tx:
            with open(tx.path("prod.cfg"), "w") as f:
                f.write("dangerous content")
        # prod.cfg is untouched; changes went to a shadow file
    """

    def __init__(self, filepaths, strategy) -> None:
        self._shadow_dir: str = tempfile.mkdtemp(prefix="safefile_dry_")
        self._map: Dict[str, str] = {}
        for fp in filepaths:
            shadow = os.path.join(self._shadow_dir, os.path.basename(fp) + ".dry")
            if os.path.exists(fp):
                if os.path.isdir(fp):
                    shutil.copytree(fp, shadow)
                else:
                    shutil.copy2(fp, shadow)
            self._map[fp] = shadow

    def path(self, original: str) -> str:
        """Return the shadow path to use instead of the real path."""
        if original not in self._map:
            shadow = os.path.join(self._shadow_dir, os.path.basename(original) + ".dry")
            self._map[original] = shadow
        return self._map[original]

    def shadow_dir(self) -> str:
        return self._shadow_dir

    # ── no-op compatibility shims ─────────────────────────────────────────────

    def touch(self, *paths: str) -> None:
        """No-op: dry_run mode never backs up, so touch is meaningless."""

    def savepoint(self) -> "_DryRunSavepoint":
        """Returns a no-op savepoint compatible object."""
        return _DryRunSavepoint()

    def rollback_to(self, sp: "_DryRunSavepoint") -> None:
        """No-op: dry_run mode has no real state to roll back."""

    def cleanup(self) -> None:
        if os.path.isdir(self._shadow_dir):
            shutil.rmtree(self._shadow_dir)


class _DryRunSavepoint:
    """Placeholder returned by DryRunProxy.savepoint(). Does nothing."""

    def restore(self) -> None:
        pass

    def discard(self) -> None:
        pass
