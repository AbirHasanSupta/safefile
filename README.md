# safefile

**Atomic, transactional file modifications – automatic rollback on failure.**

Protect your files from being left corrupted or inconsistent if your script crashes.

Wrap file-changing code in a `with transaction(...)` block. If an exception occurs, all protected files are instantly restored to their previous state.

## Installation

```bash
pip install safefile
```

## Quick start

```python
from safefile import transaction

# Basic protection (copy strategy)
with transaction("config.yaml", "data.csv"):
    update_config("config.yaml")
    update_data("data.csv")
    # If anything crashes, both files roll back.

# For large files: use strategy="hardlink" (instant, zero-space copy)
with transaction("database.sqlite", strategy="hardlink"):
    compact_database("database.sqlite")
```

## Strategies

* `copy` (default): works anywhere, creates an actual backup.
* `hardlink`: near-instant snapshots for huge files. Requires the backup temp directory to be on the same filesystem; falls back to copy if hardlinks are unsupported.

## How it works

* On entering the block, a backup of each existing file is created (copy or hardlink).
* If the block completes without an exception, the backups are deleted – changes are committed.
* If an exception is raised, the original files are restored from the backups; newly created files are removed.

## License

MIT