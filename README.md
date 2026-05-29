# safefile

**Atomic, transactional file modifications – automatic rollback on failure.**

Protect files and directories from being left corrupted if your script crashes.

## Installation

```bash
pip install safefile
```

## Quick start

```python
from safefile import transaction

# Basic protection
with transaction("config.yaml", "data.csv"):
    update_config("config.yaml")
    update_data("data.csv")
    # crash here → both files restored automatically
```

## Strategies

### `copy` (default)
Safe copy via `shutil.copy2`. Works everywhere.

```python
with transaction("config.yaml", strategy="copy"):
    ...
```

### `hardlink`
Near-instant snapshot via `os.link()` — no data duplication. Falls back to `copy` automatically if the temp directory is on a different filesystem.

```python
with transaction("big_file.bin", strategy="hardlink"):
    ...
```

## Directory support

Pass a directory path — the entire tree is snapshotted and restored.

```python
with transaction("configs/", "data.csv"):
    shutil.rmtree("configs/")
    os.makedirs("configs/")
    rebuild_configs("configs/")
    update_data("data.csv")
    # any failure → configs/ and data.csv both restored
```

## Hooks

```python
with transaction(
    "db.sqlite",
    on_commit=lambda: logger.info("committed"),
    on_rollback=lambda: alert.send("rolled back"),
):
    modify_db("db.sqlite")
```

## How it works

1. On enter: each existing file/directory is backed up (using the chosen strategy).
2. On success: backups are discarded.
3. On exception: all originals are restored; new files/dirs created inside the block are deleted.

## License

MIT