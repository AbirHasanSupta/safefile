import os
import shutil
import tempfile
from typing import Dict, Optional


class DryRunProxy:
    """
    Returned from Transaction.__enter__ when dry_run=True.
    Intercepts writes by redirecting paths to shadow copies.
    The original files are never modified.

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

    def cleanup(self) -> None:
        if os.path.isdir(self._shadow_dir):
            shutil.rmtree(self._shadow_dir)