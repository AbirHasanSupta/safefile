# safefile

**Atomic, transactional file modifications – automatic rollback on failure.**

Protect files and directories from being left corrupted if your script crashes. Zero dependencies, pure Python 3.8+.

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
# on rollback: raises ChecksumMismatchError if restored file doesn't match original checksum
```

---

## Streaming backup with progress

For large files, backup runs in configurable chunks and reports progress. The backup streams through a `.part` file and is atomically renamed on completion — a mid-copy crash never leaves a half-written backup on disk.

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

## Crash recovery (journal)

By default, every transaction writes a journal file into its temp directory before touching any files. If your process is killed mid-transaction (SIGKILL, power loss, OOM), the backup and journal survive. On your next startup, call `recover_orphaned()` to detect and restore them automatically.

```python
from safefile import recover_orphaned

# call once at app startup, before any other file work
recovered = recover_orphaned(verbose=True)
print(f"{recovered} orphaned transaction(s) recovered")
```

To inspect pending orphans without restoring them, use `find_orphaned_journals()`:

```python
from safefile import find_orphaned_journals

orphans = find_orphaned_journals()
for o in orphans:
    print(o["temp_dir"], list(o["backups"].keys()))
```

The journal is written atomically (`fsync` + `os.replace`) before any file is modified, and marked `committed` on clean exit. Only `open` journals trigger recovery — committed ones are never touched.

Disable journaling for short-lived or test transactions:

```python
with transaction("tmp.txt", journal=False):
    ...
```

---

## Resilient rollback

If restoring one file fails during rollback (e.g. a permission error or full disk), safefile continues restoring all remaining files and then raises a single `RollbackError` summarising every failure. No file is silently skipped.

---

## All options

```python
transaction(
    *filepaths,
    strategy="copy",        # "copy" (default) or "hardlink"
    on_commit=None,         # callable fired on success
    on_rollback=None,       # callable fired on failure
    lazy=False,             # defer backup until tx.touch(path) is called
    dry_run=False,          # redirect writes to shadow copies, never touch originals
    verify=False,           # SHA-256 checksum check after rollback
    journal=True,           # write crash-recovery journal (default on)
    chunk_size=52428800,    # streaming threshold in bytes (default 50 MB)
    on_progress=None,       # callable(int 0–100) during large-file backup
)
```

`async_transaction` accepts the same options.

---

## CLI (`safefile` command)

### Protect files around any shell command

```bash
safefile run --protect config.yaml data.csv -- python deploy.py
```

If `deploy.py` exits non-zero or crashes, both files are restored automatically.

```bash
# options
safefile run --protect FILE [FILE ...]   # files/dirs to protect (required)
             --strategy copy|hardlink    # backup strategy (default: copy)
             --verify                    # SHA-256 verify after restore
             --no-journal                # skip crash-recovery journal
             --progress                  # show backup progress for large files
             --verbose / -v              # print transaction lifecycle messages
             -- COMMAND [ARGS ...]       # command to run
```

### Recover after a crash

```bash
safefile recover              # interactive prompt listing what will be restored
safefile recover --yes        # skip confirmation
safefile recover --dry-run    # show what would be recovered, do nothing
safefile recover --verbose    # print each file as it is restored
```

### Check for pending recoveries

```bash
safefile status    # exits 0 if clean, exits 1 if orphaned transactions exist
```

Useful in CI pre-flight checks or deploy scripts.

---

## pytest plugin

safefile registers automatically as a pytest plugin when installed — no imports or configuration needed. Use `safefile_guard` as a fixture argument and call `guard.protect(*paths)` to snapshot files; they are restored unconditionally after the test, whether it passes or fails.

```python
def test_deploy(safefile_guard, tmp_path):
    config = str(tmp_path / "config.yaml")
    write_fixture(config)
    safefile_guard.protect(config)    # snapshot it
    run_deploy_pipeline(config)       # mutates config
    assert something_about(config)
    # config is restored automatically after the test — no teardown needed
```

`protect()` can be called at any point during the test, including mid-test after a first stage completes:

```python
def test_multi_stage(safefile_guard):
    safefile_guard.protect("a.yaml")
    stage_one("a.yaml")
    safefile_guard.protect("b.db")    # added mid-test
    stage_two("b.db")
    # both files restored on teardown
```

Three fixture variants are available — all auto-registered, no imports needed:

```python
def test_a(safefile_guard):           ...  # copy strategy (default)
def test_b(safefile_guard_hardlink):  ...  # hardlink strategy — faster for large files
def test_c(safefile_guard_verify):    ...  # copy + SHA-256 checksum verify on restore
```

If the plugin fixtures are not auto-discovered (e.g. the package is not installed via `pip install -e .`), add a `conftest.py` at your project root:

```python
from safefile._pytest_plugin import safefile_guard, safefile_guard_hardlink, safefile_guard_verify
```

---

## Exceptions

All exceptions inherit from `SafefileError` so you can catch everything with one clause.

| Exception | Raised when |
|---|---|
| `SafefileError` | Base class for all safefile errors |
| `BackupError` | A file/directory backup fails at entry |
| `RestoreError` | A single file restore fails during rollback |
| `RollbackError` | Rollback completes but one or more restores failed — `.errors` holds each `RestoreError` |
| `ChecksumMismatchError` | `verify=True` and the restored file's SHA-256 doesn't match the original backup |
| `JournalError` | Reading or writing the crash-recovery journal fails |
| `StrategyError` | An unknown strategy name is passed to `transaction()` |

```python
from safefile import transaction
from safefile import SafefileError, RollbackError, ChecksumMismatchError

try:
    with transaction("critical.db", verify=True):
        rewrite_database("critical.db")
except ChecksumMismatchError as e:
    print(f"Checksum mismatch on {e.path}: expected {e.expected}, got {e.got}")
except RollbackError as e:
    for err in e.errors:
        print(f"Restore failed: {err}")
except SafefileError as e:
    print(f"safefile error: {e}")
```

---

## License

MIT