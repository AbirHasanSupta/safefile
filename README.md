# safefile

**Atomic, transactional file modifications – automatic rollback on failure.**

Protect files and directories from being left corrupted if your script crashes. Zero dependencies, pure Python 3.7+.

## Installation

```bash
pip install safefile
```

## Quick start

```python
from safefile import transaction

with transaction("config.yaml", "data.csv"):
    update_config("config.yaml")
    update_data("data.csv")
    # crash here → both files restored automatically
```

## How it works

1. On enter: each existing file/directory is backed up using the chosen strategy.
2. On success: backups are silently discarded.
3. On exception: all originals are restored; any new files/dirs created inside the block are deleted.

---

## Backup strategies

### `copy` (default)
Safe copy via `shutil.copy2`. Works on every OS and filesystem.

```python
with transaction("config.yaml", strategy="copy"):
    ...
```

### `hardlink`
Near-instant snapshot via `os.link()` — no data duplication on disk. Automatically falls back to `copy` if the temp directory is on a different filesystem.

```python
with transaction("big_file.bin", strategy="hardlink"):
    ...
```

---

## Directory support

Pass a directory path — the entire tree is snapshotted and restored on failure.

```python
with transaction("configs/", "data.csv"):
    shutil.rmtree("configs/")
    rebuild_configs("configs/")
    update_data("data.csv")
    # any failure → configs/ and data.csv both restored
```

---

## Hooks

Run callbacks on commit or rollback — useful for logging, alerting, or audit trails.

```python
with transaction(
    "db.sqlite",
    on_commit=lambda: logger.info("committed"),
    on_rollback=lambda: alerts.send("rolled back"),
):
    modify_db("db.sqlite")
```

---

## Savepoints

Snapshot state mid-transaction and roll back to that point without aborting the whole block.

```python
with transaction("a.txt", "b.txt") as tx:
    modify_a("a.txt")
    sp = tx.savepoint()       # snapshot here
    try:
        risky_modify_b("b.txt")
    except Exception:
        tx.rollback_to(sp)    # b.txt reverts, a.txt stays modified
        safe_fallback_b("b.txt")
# commits with a.txt modified + safe b.txt
```

Multiple savepoints stack. Rolling back to an earlier one automatically discards all later ones.

---

## Lazy backup

Only back up files that are actually going to be written. Saves I/O when protecting many files but only touching a few.

```python
with transaction("a.txt", "b.txt", "c.txt", lazy=True) as tx:
    tx.touch("a.txt")        # backup triggered here, not at block entry
    modify_a("a.txt")
    # b.txt and c.txt are never backed up — no I/O cost
```

---

## Async support

Drop-in async context manager. Backup and restore run in a thread pool so the event loop is never blocked.

```python
from safefile import async_transaction

async def deploy():
    async with async_transaction("config.yaml", "state.json"):
        await write_config("config.yaml")
        await update_state("state.json")
```

All the same options (`strategy`, `lazy`, `verify`, `on_commit`, etc.) work on `async_transaction`.

---

## Dry run

Test a destructive operation without touching the real files. `tx.path()` redirects writes to shadow copies.

```python
with transaction("prod.cfg", dry_run=True) as tx:
    with open(tx.path("prod.cfg"), "w") as f:
        f.write(generate_config())
# prod.cfg is completely untouched; shadow copies are cleaned up
```

---

## Checksum verification

After rollback, verify the restored file matches the original backup's SHA-256. Catches corrupted backups before silently leaving a broken file.

```python
with transaction("critical.db", verify=True):
    rewrite_database("critical.db")
# on rollback: raises RuntimeError if restored file doesn't match original checksum
```

---

## Streaming backup with progress

For large files, backup runs in configurable chunks and reports progress.

```python
with transaction(
    "10gb.iso",
    chunk_size=10 * 1024 * 1024,           # 10 MB chunks
    on_progress=lambda pct: print(f"{pct}%"),
):
    replace_image("10gb.iso")
```

`on_progress` is only called when the file exceeds `chunk_size`. Small files use a direct copy with no overhead.

---

## All options

```python
transaction(
    *filepaths,
    strategy="copy",        # "copy" (default) or "hardlink"
    on_commit=None,         # callable, fired on success
    on_rollback=None,       # callable, fired on failure
    lazy=False,             # defer backup until tx.touch(path) is called
    dry_run=False,          # redirect writes to shadow copies, never touch originals
    verify=False,           # SHA-256 checksum check after rollback
    chunk_size=52428800,    # bytes threshold for chunked streaming (default 50 MB)
    on_progress=None,       # callable(int) receiving 0–100 during large-file backup
)
```

`async_transaction` accepts the same options.

---

## License

MIT