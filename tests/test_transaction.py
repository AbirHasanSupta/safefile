import asyncio
import json
import os
import shutil
import tempfile
import threading

import pytest

from safefile import transaction, async_transaction, recover_orphaned, find_orphaned_journals
from safefile._exceptions import (
    SafefileError,
    BackupError,
    RestoreError,
    RollbackError,
    ChecksumMismatchError,
    JournalError,
    StrategyError,
)
from safefile._journal import write_journal, mark_committed, _journal_path
from safefile._pytest_plugin import SafefileGuard
from safefile._cli import main


# ── helpers ───────────────────────────────────────────────────────────────────

def write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

def rm(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


# ── copy strategy ─────────────────────────────────────────────────────────────

def test_commit_success():
    write("c_commit.txt", "original")
    with transaction("c_commit.txt"):
        write("c_commit.txt", "modified")
    assert read("c_commit.txt") == "modified"
    rm("c_commit.txt")

def test_rollback_on_exception():
    write("c_rollback.txt", "original")
    with pytest.raises(ValueError):
        with transaction("c_rollback.txt"):
            write("c_rollback.txt", "changed")
            raise ValueError
    assert read("c_rollback.txt") == "original"
    rm("c_rollback.txt")

def test_rollback_removes_new_files():
    with pytest.raises(RuntimeError):
        with transaction("c_new.txt"):
            write("c_new.txt", "something")
            raise RuntimeError
    assert not os.path.exists("c_new.txt")

def test_multiple_files():
    write("c_f1.txt", "one")
    write("c_f2.txt", "two")
    with pytest.raises(ValueError):
        with transaction("c_f1.txt", "c_f2.txt"):
            write("c_f1.txt", "A")
            write("c_f2.txt", "B")
            raise ValueError
    assert read("c_f1.txt") == "one"
    assert read("c_f2.txt") == "two"
    rm("c_f1.txt")
    rm("c_f2.txt")


# ── hardlink strategy ─────────────────────────────────────────────────────────

def test_hardlink_commit():
    write("h_commit.txt", "original")
    with transaction("h_commit.txt", strategy="hardlink"):
        write("h_commit.txt", "modified")
    assert read("h_commit.txt") == "modified"
    rm("h_commit.txt")

def test_hardlink_rollback():
    write("h_rollback.txt", "original")
    with pytest.raises(ValueError):
        with transaction("h_rollback.txt", strategy="hardlink"):
            write("h_rollback.txt", "changed")
            raise ValueError
    assert read("h_rollback.txt") == "original"
    rm("h_rollback.txt")

def test_hardlink_new_file_removed_on_rollback():
    with pytest.raises(RuntimeError):
        with transaction("h_new.txt", strategy="hardlink"):
            write("h_new.txt", "data")
            raise RuntimeError
    assert not os.path.exists("h_new.txt")

def test_hardlink_uses_fast_path_for_atomic_replace(tmp_path):
    """
    Phase-1: when a file is atomically replaced (new inode) the restore must
    use the hardlink directly and the shadow copy must be cleaned up afterward.
    """
    f = str(tmp_path / "hl_atomic.txt")
    write(f, "original")
    inode_before = os.stat(f).st_ino

    with pytest.raises(ValueError):
        with transaction(f, strategy="hardlink") as tx:
            backup_dir = tx._temp_dir
            tmp_f = f + ".new"
            write(tmp_f, "atomically replaced")
            os.replace(tmp_f, f)
            assert os.stat(f).st_ino != inode_before
            raise ValueError

    assert read(f) == "original"
    assert not os.path.isdir(backup_dir)

def test_hardlink_uses_shadow_for_inplace_write(tmp_path):
    """
    Phase-1: when a file is written in-place (same inode) the restore must
    fall back to the shadow copy to recover the original content.
    """
    f = str(tmp_path / "hl_inplace.txt")
    write(f, "original")
    inode_before = os.stat(f).st_ino

    with pytest.raises(ValueError):
        with transaction(f, strategy="hardlink"):
            write(f, "changed in-place")
            assert os.stat(f).st_ino == inode_before
            raise ValueError

    assert read(f) == "original"

def test_hardlink_multiple_files_rollback(tmp_path):
    fa = str(tmp_path / "hl_fa.txt")
    fb = str(tmp_path / "hl_fb.txt")
    write(fa, "fa-orig")
    write(fb, "fb-orig")
    with pytest.raises(ValueError):
        with transaction(fa, fb, strategy="hardlink"):
            write(fa, "fa-changed")
            write(fb, "fb-changed")
            raise ValueError
    assert read(fa) == "fa-orig"
    assert read(fb) == "fb-orig"

def test_hardlink_binary_file_rollback(tmp_path):
    f = str(tmp_path / "hl_bin.bin")
    data = bytes(range(256))
    with open(f, "wb") as fh:
        fh.write(data)
    with pytest.raises(RuntimeError):
        with transaction(f, strategy="hardlink"):
            with open(f, "wb") as fh:
                fh.write(b"\xff" * 256)
            raise RuntimeError
    with open(f, "rb") as fh:
        assert fh.read() == data


# ── directory support ─────────────────────────────────────────────────────────

def test_dir_commit():
    os.makedirs("d_commit_dir", exist_ok=True)
    write("d_commit_dir/a.txt", "original")
    with transaction("d_commit_dir"):
        write("d_commit_dir/a.txt", "modified")
        write("d_commit_dir/b.txt", "new")
    assert read("d_commit_dir/a.txt") == "modified"
    assert read("d_commit_dir/b.txt") == "new"
    rm("d_commit_dir")

def test_dir_rollback():
    os.makedirs("d_rollback_dir", exist_ok=True)
    write("d_rollback_dir/a.txt", "original")
    with pytest.raises(ValueError):
        with transaction("d_rollback_dir"):
            write("d_rollback_dir/a.txt", "corrupted")
            write("d_rollback_dir/extra.txt", "extra")
            raise ValueError
    assert read("d_rollback_dir/a.txt") == "original"
    assert not os.path.exists("d_rollback_dir/extra.txt")
    rm("d_rollback_dir")

def test_new_dir_removed_on_rollback():
    with pytest.raises(RuntimeError):
        with transaction("d_new_dir"):
            os.makedirs("d_new_dir")
            write("d_new_dir/x.txt", "x")
            raise RuntimeError
    assert not os.path.exists("d_new_dir")

def test_dir_hardlink_rollback():
    os.makedirs("d_hl_dir/sub", exist_ok=True)
    write("d_hl_dir/a.txt", "original")
    write("d_hl_dir/sub/b.txt", "sub-original")
    with pytest.raises(ValueError):
        with transaction("d_hl_dir", strategy="hardlink"):
            write("d_hl_dir/a.txt", "changed")
            write("d_hl_dir/sub/b.txt", "sub-changed")
            raise ValueError
    assert read("d_hl_dir/a.txt") == "original"
    assert read("d_hl_dir/sub/b.txt") == "sub-original"
    rm("d_hl_dir")


# ── hooks ─────────────────────────────────────────────────────────────────────

def test_on_commit_hook_called():
    called = []
    write("hook_commit.txt", "x")
    with transaction("hook_commit.txt", on_commit=lambda: called.append("c")):
        write("hook_commit.txt", "y")
    assert called == ["c"]
    rm("hook_commit.txt")

def test_on_rollback_hook_called():
    called = []
    write("hook_rb.txt", "x")
    with pytest.raises(RuntimeError):
        with transaction("hook_rb.txt", on_rollback=lambda: called.append("r")):
            raise RuntimeError
    assert called == ["r"]
    rm("hook_rb.txt")

def test_on_commit_not_called_on_failure():
    called = []
    write("hook_nc.txt", "x")
    with pytest.raises(RuntimeError):
        with transaction("hook_nc.txt", on_commit=lambda: called.append("c")):
            raise RuntimeError
    assert called == []
    rm("hook_nc.txt")

def test_on_rollback_not_called_on_success():
    called = []
    write("hook_nr.txt", "x")
    with transaction("hook_nr.txt", on_rollback=lambda: called.append("r")):
        write("hook_nr.txt", "y")
    assert called == []
    rm("hook_nr.txt")

def test_invalid_strategy_raises():
    with pytest.raises(StrategyError, match="Unknown strategy"):
        transaction("any.txt", strategy="magic")


# ── savepoints ────────────────────────────────────────────────────────────────

def test_savepoint_rollback_partial():
    write("sp_a.txt", "a-orig")
    write("sp_b.txt", "b-orig")
    with transaction("sp_a.txt", "sp_b.txt") as tx:
        write("sp_a.txt", "a-step1")
        sp = tx.savepoint()
        write("sp_b.txt", "b-step2")
        tx.rollback_to(sp)
        assert read("sp_a.txt") == "a-step1"
        assert read("sp_b.txt") == "b-orig"
        write("sp_b.txt", "b-final")
    assert read("sp_a.txt") == "a-step1"
    assert read("sp_b.txt") == "b-final"
    rm("sp_a.txt")
    rm("sp_b.txt")

def test_savepoint_full_rollback_still_works():
    write("sp_full.txt", "original")
    with pytest.raises(ValueError):
        with transaction("sp_full.txt") as tx:
            write("sp_full.txt", "step1")
            sp = tx.savepoint()
            write("sp_full.txt", "step2")
            tx.rollback_to(sp)
            write("sp_full.txt", "step3")
            raise ValueError
    assert read("sp_full.txt") == "original"
    rm("sp_full.txt")

def test_multiple_savepoints_stack():
    write("sp_m.txt", "v0")
    with transaction("sp_m.txt") as tx:
        write("sp_m.txt", "v1")
        sp1 = tx.savepoint()
        write("sp_m.txt", "v2")
        tx.savepoint()
        write("sp_m.txt", "v3")
        tx.rollback_to(sp1)
        assert read("sp_m.txt") == "v1"
    assert read("sp_m.txt") == "v1"
    rm("sp_m.txt")

def test_savepoint_new_file_removed_on_rollback():
    write("sp_exist.txt", "exists")
    with transaction("sp_exist.txt", "sp_nf.txt") as tx:
        sp = tx.savepoint()
        write("sp_nf.txt", "created after savepoint")
        tx.rollback_to(sp)
        assert not os.path.exists("sp_nf.txt")
    rm("sp_exist.txt")

def test_savepoint_rollback_to_does_not_leave_stale_new_paths(tmp_path):
    """
    Phase-1 regression: after rollback_to removes a file, that path must be
    removed from _new_paths so the outer rollback does not attempt a second
    (failing) deletion.
    """
    f = str(tmp_path / "sp_stale.txt")
    write(f, "original")
    # No exception — just check no error on final __exit__
    with transaction(f) as tx:
        sp = tx.savepoint()
        # create a brand-new file inside the savepoint scope
        new_f = str(tmp_path / "sp_new.txt")
        write(new_f, "new")
        tx._register(new_f)
        tx.rollback_to(sp)
        # new_f should be gone now; it must not be in _new_paths still
        assert not os.path.exists(new_f)
        # confirm outer rollback won't choke on a missing path
        assert new_f not in tx._new_paths


# ── lazy backup ───────────────────────────────────────────────────────────────

def test_lazy_backup_only_touched_files():
    write("lz_a.txt", "a-orig")
    write("lz_b.txt", "b-orig")
    with pytest.raises(RuntimeError):
        with transaction("lz_a.txt", "lz_b.txt", lazy=True) as tx:
            tx.touch("lz_a.txt")
            write("lz_a.txt", "a-changed")
            write("lz_b.txt", "b-changed")
            raise RuntimeError
    assert read("lz_a.txt") == "a-orig"
    assert read("lz_b.txt") == "b-changed"
    rm("lz_a.txt")
    rm("lz_b.txt")

def test_lazy_commit_unchanged():
    write("lz_c.txt", "original")
    with transaction("lz_c.txt", lazy=True) as tx:
        tx.touch("lz_c.txt")
        write("lz_c.txt", "modified")
    assert read("lz_c.txt") == "modified"
    rm("lz_c.txt")

def test_lazy_no_touch_no_backup():
    write("lz_skip.txt", "original")
    with pytest.raises(RuntimeError):
        with transaction("lz_skip.txt", lazy=True):
            write("lz_skip.txt", "changed")
            raise RuntimeError
    assert read("lz_skip.txt") == "changed"
    rm("lz_skip.txt")


# ── async ─────────────────────────────────────────────────────────────────────

def test_async_commit():
    async def run():
        write("async_c.txt", "original")
        async with async_transaction("async_c.txt"):
            write("async_c.txt", "modified")
        assert read("async_c.txt") == "modified"
        rm("async_c.txt")
    asyncio.run(run())

def test_async_rollback():
    async def run():
        write("async_rb.txt", "original")
        with pytest.raises(ValueError):
            async with async_transaction("async_rb.txt"):
                write("async_rb.txt", "changed")
                raise ValueError
        assert read("async_rb.txt") == "original"
        rm("async_rb.txt")
    asyncio.run(run())

def test_async_uses_get_running_loop():
    """
    Phase-1 regression: AsyncTransaction must use asyncio.get_running_loop(),
    not the deprecated asyncio.get_event_loop().
    """
    import inspect
    import safefile._transaction as mod
    src = inspect.getsource(mod.AsyncTransaction)
    assert "get_running_loop" in src
    assert "get_event_loop" not in src


# ── dry run ───────────────────────────────────────────────────────────────────

def test_dry_run_does_not_modify_original():
    write("dry.txt", "original")
    with transaction("dry.txt", dry_run=True) as tx:
        shadow = tx.path("dry.txt")
        with open(shadow, "w") as f:
            f.write("dangerous")
    assert read("dry.txt") == "original"
    rm("dry.txt")

def test_dry_run_shadow_path_is_writable():
    write("dry_w.txt", "original")
    with transaction("dry_w.txt", dry_run=True) as tx:
        shadow = tx.path("dry_w.txt")
        assert os.path.exists(shadow)
        with open(shadow, "w") as f:
            f.write("shadow content")
        assert read(shadow) == "shadow content"
    assert read("dry_w.txt") == "original"
    rm("dry_w.txt")

def test_dry_run_shadow_cleaned_up_after_block():
    write("dry_clean.txt", "x")
    with transaction("dry_clean.txt", dry_run=True) as tx:
        shadow_dir = tx.shadow_dir()
    assert not os.path.isdir(shadow_dir)
    rm("dry_clean.txt")

def test_dry_run_new_file_not_created():
    with transaction("dry_new.txt", dry_run=True) as tx:
        shadow = tx.path("dry_new.txt")
        with open(shadow, "w") as f:
            f.write("something")
    assert not os.path.exists("dry_new.txt")

def test_dry_run_combined_with_lazy_no_attribute_error():
    """
    Phase-1 regression: dry_run=True + lazy=True must not raise AttributeError.
    DryRunProxy exposes a no-op touch().
    """
    write("dry_lazy.txt", "original")
    with transaction("dry_lazy.txt", dry_run=True, lazy=True) as tx:
        tx.touch("dry_lazy.txt")  # must not raise AttributeError
        with open(tx.path("dry_lazy.txt"), "w") as f:
            f.write("shadow write")
    assert read("dry_lazy.txt") == "original"
    rm("dry_lazy.txt")

def test_dry_run_savepoint_no_attribute_error():
    """
    Phase-1 regression: tx.savepoint() and tx.rollback_to() on a DryRunProxy
    must not raise AttributeError.
    """
    write("dry_sp.txt", "original")
    with transaction("dry_sp.txt", dry_run=True) as tx:
        sp = tx.savepoint()
        tx.rollback_to(sp)
    assert read("dry_sp.txt") == "original"
    rm("dry_sp.txt")


# ── checksum verify ───────────────────────────────────────────────────────────

def test_verify_passes_on_clean_restore():
    write("vf_ok.txt", "original")
    with pytest.raises(RuntimeError):
        with transaction("vf_ok.txt", verify=True):
            write("vf_ok.txt", "changed")
            raise RuntimeError
    assert read("vf_ok.txt") == "original"
    rm("vf_ok.txt")

def test_verify_no_error_on_commit():
    write("vf_commit.txt", "original")
    with transaction("vf_commit.txt", verify=True):
        write("vf_commit.txt", "modified")
    assert read("vf_commit.txt") == "modified"
    rm("vf_commit.txt")


# ── streaming / progress ──────────────────────────────────────────────────────

def test_progress_callback_called_for_large_files(tmp_path):
    large = str(tmp_path / "large.bin")
    with open(large, "wb") as f:
        f.write(b"x" * (6 * 1024 * 1024))
    progress = []
    with pytest.raises(RuntimeError):
        with transaction(
            large,
            chunk_size=2 * 1024 * 1024,
            on_progress=progress.append,
        ):
            with open(large, "w") as f:
                f.write("corrupted")
            raise RuntimeError
    assert len(progress) > 0
    assert all(0 <= p <= 100 for p in progress)
    with open(large) as f:
        assert f.read() == "x" * (6 * 1024 * 1024)

def test_progress_not_called_for_small_files(tmp_path):
    small = str(tmp_path / "small.txt")
    write(small, "tiny")
    progress = []
    with pytest.raises(RuntimeError):
        with transaction(
            small,
            chunk_size=50 * 1024 * 1024,
            on_progress=progress.append,
        ):
            raise RuntimeError
    assert progress == []
    rm(small)


# ── journal / crash recovery ──────────────────────────────────────────────────

def test_journal_written_on_enter(tmp_path):
    f = str(tmp_path / "jrn.txt")
    write(f, "original")
    with transaction(f, journal=True) as tx:
        journal_file = _journal_path(tx._temp_dir)
        assert os.path.exists(journal_file)
        with open(journal_file) as jf:
            rec = json.load(jf)
        assert rec["status"] == "open"
        assert f in rec["backups"]

def test_journal_marked_committed_on_success(tmp_path):
    f = str(tmp_path / "jrn_c.txt")
    write(f, "original")
    captured_dir = []
    with transaction(f, journal=True) as tx:
        captured_dir.append(tx._temp_dir)
        write(f, "modified")
    assert not os.path.isdir(captured_dir[0])

def test_journal_absent_when_disabled(tmp_path):
    f = str(tmp_path / "nojrn.txt")
    write(f, "original")
    with transaction(f, journal=False) as tx:
        td = tx._temp_dir
        journal_file = _journal_path(td)
        assert not os.path.exists(journal_file)

def test_recover_orphaned_restores_files(tmp_path):
    f = str(tmp_path / "orphan.txt")
    write(f, "original")
    td = tempfile.mkdtemp(prefix="safefile_")
    backup = os.path.join(td, "orphan.txt.bak")
    shutil.copy2(f, backup)
    write(f, "corrupted")
    write_journal(
        temp_dir=td,
        filepaths=[f],
        backups={f: backup},
        new_paths=set(),
        dirs=set(),
        strategy="copy",
    )
    count = recover_orphaned()
    assert count >= 1
    assert read(f) == "original"
    assert not os.path.isdir(td)

def test_recover_orphaned_removes_new_files(tmp_path):
    new_f = str(tmp_path / "new_orphan.txt")
    write(new_f, "created mid-transaction")
    td = tempfile.mkdtemp(prefix="safefile_")
    write_journal(
        temp_dir=td,
        filepaths=[new_f],
        backups={},
        new_paths={new_f},
        dirs=set(),
        strategy="copy",
    )
    count = recover_orphaned()
    assert count >= 1
    assert not os.path.exists(new_f)
    assert not os.path.isdir(td)

def test_recover_orphaned_ignores_committed(tmp_path):
    f = str(tmp_path / "committed.txt")
    write(f, "final")
    td = tempfile.mkdtemp(prefix="safefile_")
    backup = os.path.join(td, "committed.txt.bak")
    shutil.copy2(f, backup)
    write_journal(
        temp_dir=td,
        filepaths=[f],
        backups={f: backup},
        new_paths=set(),
        dirs=set(),
        strategy="copy",
    )
    mark_committed(td)
    recover_orphaned()
    assert read(f) == "final"
    shutil.rmtree(td, ignore_errors=True)

def test_find_orphaned_journals_returns_open_only(tmp_path):
    td = tempfile.mkdtemp(prefix="safefile_")
    write_journal(
        temp_dir=td,
        filepaths=[],
        backups={},
        new_paths=set(),
        dirs=set(),
        strategy="copy",
    )
    orphans = find_orphaned_journals()
    assert any(o["temp_dir"] == td for o in orphans)
    shutil.rmtree(td, ignore_errors=True)

def test_journal_disabled_does_not_affect_rollback(tmp_path):
    f = str(tmp_path / "nj.txt")
    write(f, "original")
    with pytest.raises(ValueError):
        with transaction(f, journal=False):
            write(f, "changed")
            raise ValueError
    assert read(f) == "original"


# ── resilient rollback ────────────────────────────────────────────────────────

def test_rollback_continues_after_single_file_restore_error(tmp_path, monkeypatch):
    fa = str(tmp_path / "ra.txt")
    fb = str(tmp_path / "rb.txt")
    write(fa, "a-orig")
    write(fb, "b-orig")

    original_restore = __import__(
        "safefile._strategies", fromlist=["CopyStrategy"]
    ).CopyStrategy.restore

    call_count = [0]

    def flaky_restore(self, backup, original):
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("simulated disk error on first restore")
        original_restore(self, backup, original)

    monkeypatch.setattr("safefile._strategies.CopyStrategy.restore", flaky_restore)

    with pytest.raises((ValueError, RollbackError)):
        with transaction(fa, fb):
            write(fa, "a-changed")
            write(fb, "b-changed")
            raise ValueError

    assert read(fb) == "b-orig"

def test_rollback_error_is_safefile_error(tmp_path, monkeypatch):
    """RollbackError must be a subclass of SafefileError."""
    f = str(tmp_path / "re.txt")
    write(f, "orig")

    def always_fail(self, backup, original):
        raise OSError("disk full")

    monkeypatch.setattr("safefile._strategies.CopyStrategy.restore", always_fail)

    with pytest.raises(SafefileError):
        with transaction(f):
            write(f, "changed")
            raise ValueError


# ── atomic streaming ──────────────────────────────────────────────────────────

def test_partial_backup_cleaned_up_on_interrupted_stream(tmp_path, monkeypatch):
    large = str(tmp_path / "large.bin")
    with open(large, "wb") as f:
        f.write(b"x" * (4 * 1024 * 1024))

    def crash_mid_stream(self, src, dest, size):
        part = dest + ".part"
        with open(part, "wb") as f:
            f.write(b"partial")
        raise IOError("simulated mid-stream failure")

    monkeypatch.setattr(
        "safefile._strategies.CopyStrategy._chunked_copy", crash_mid_stream
    )

    with pytest.raises((IOError, RuntimeError, BackupError)):
        with transaction(
            large,
            chunk_size=1 * 1024 * 1024,
            on_progress=lambda p: None,
        ):
            pass

    for fname in os.listdir(tmp_path):
        assert not fname.endswith(".part"), f"orphaned .part file: {fname}"


# ── exception hierarchy ───────────────────────────────────────────────────────

def test_strategy_error_is_safefile_error():
    with pytest.raises(SafefileError):
        transaction("x.txt", strategy="nonexistent")

def test_backup_error_attributes():
    err = BackupError("/some/path", "disk full")
    assert err.path == "/some/path"
    assert err.reason == "disk full"
    assert isinstance(err, SafefileError)

def test_restore_error_attributes():
    err = RestoreError("/some/path", "permission denied")
    assert err.path == "/some/path"
    assert err.reason == "permission denied"
    assert isinstance(err, SafefileError)

def test_rollback_error_attributes():
    inner = OSError("boom")
    err = RollbackError([inner])
    assert err.errors == [inner]
    assert isinstance(err, SafefileError)

def test_checksum_mismatch_error_attributes():
    err = ChecksumMismatchError("/f", "aaa", "bbb")
    assert err.path == "/f"
    assert err.expected == "aaa"
    assert err.got == "bbb"
    assert isinstance(err, SafefileError)

def test_journal_error_attributes():
    err = JournalError("/tmp/j.json", "invalid JSON")
    assert err.journal_path == "/tmp/j.json"
    assert isinstance(err, SafefileError)


# ── thread safety ─────────────────────────────────────────────────────────────

def test_concurrent_register_does_not_corrupt_state(tmp_path):
    """
    Phase-1: concurrent _register() calls from multiple threads must not
    corrupt _backups or _new_paths.
    """
    files = [str(tmp_path / f"t{i}.txt") for i in range(20)]
    for f in files:
        write(f, f"content-{f}")

    tx = transaction(*files, journal=False)
    tx.__enter__()

    errors = []
    def register_all():
        for f in files:
            try:
                tx._register(f)
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=register_all) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # each file must appear exactly once in _backups
    for f in files:
        assert list(tx._backups.keys()).count(f) == 1

    tx.__exit__(None, None, None)


# ── pytest plugin ─────────────────────────────────────────────────────────────

def test_guard_restores_file_after_test(tmp_path):
    f = str(tmp_path / "guarded.txt")
    write(f, "original")
    guard = SafefileGuard()
    guard._start()
    guard.protect(f)
    write(f, "mutated by test")
    guard._stop()
    assert read(f) == "original"

def test_guard_removes_new_file_after_test(tmp_path):
    f = str(tmp_path / "new_guarded.txt")
    guard = SafefileGuard()
    guard._start()
    guard.protect(f)
    write(f, "created mid-test")
    guard._stop()
    assert not os.path.exists(f)

def test_guard_multiple_files(tmp_path):
    fa = str(tmp_path / "ga.txt")
    fb = str(tmp_path / "gb.txt")
    write(fa, "a-orig")
    write(fb, "b-orig")
    guard = SafefileGuard()
    guard._start()
    guard.protect(fa, fb)
    write(fa, "a-mutated")
    write(fb, "b-mutated")
    guard._stop()
    assert read(fa) == "a-orig"
    assert read(fb) == "b-orig"

def test_guard_protect_mid_test(tmp_path):
    fa = str(tmp_path / "mid_a.txt")
    fb = str(tmp_path / "mid_b.txt")
    write(fa, "a-orig")
    write(fb, "b-orig")
    guard = SafefileGuard()
    guard._start()
    guard.protect(fa)
    write(fa, "a-changed")
    guard.protect(fb)
    write(fb, "b-changed")
    guard._stop()
    assert read(fa) == "a-orig"
    assert read(fb) == "b-orig"

def test_guard_hardlink_strategy(tmp_path):
    f = str(tmp_path / "hl_guarded.txt")
    write(f, "original")
    guard = SafefileGuard(strategy="hardlink")
    guard._start()
    guard.protect(f)
    write(f, "mutated")
    guard._stop()
    assert read(f) == "original"

def test_guard_verify(tmp_path):
    f = str(tmp_path / "vf_guarded.txt")
    write(f, "original")
    guard = SafefileGuard(verify=True)
    guard._start()
    guard.protect(f)
    write(f, "mutated")
    guard._stop()
    assert read(f) == "original"

def test_guard_directory(tmp_path):
    d = str(tmp_path / "guarded_dir")
    os.makedirs(d)
    write(os.path.join(d, "a.txt"), "original")
    guard = SafefileGuard()
    guard._start()
    guard.protect(d)
    write(os.path.join(d, "a.txt"), "mutated")
    write(os.path.join(d, "b.txt"), "new file")
    guard._stop()
    assert read(os.path.join(d, "a.txt")) == "original"
    assert not os.path.exists(os.path.join(d, "b.txt"))

def test_guard_protect_outside_test_raises():
    guard = SafefileGuard()
    with pytest.raises(RuntimeError, match="outside of an active test"):
        guard.protect("any.txt")

def test_guard_uses_transaction_enter_exit(tmp_path):
    """
    Phase-1 regression: SafefileGuard must call Transaction.__enter__ in
    _start(), not bypass it by setting _temp_dir manually.
    """
    import safefile._pytest_plugin as plugin_mod
    import inspect
    src = inspect.getsource(plugin_mod.SafefileGuard._start)
    assert "__enter__" in src

def test_safefile_guard_fixture(safefile_guard, tmp_path):
    f = str(tmp_path / "fixture_test.txt")
    write(f, "original")
    safefile_guard.protect(f)
    write(f, "modified by test")
    assert read(f) == "modified by test"

def test_safefile_guard_fixture_restored_independently(tmp_path):
    f = str(tmp_path / "fixture_test.txt")
    write(f, "fresh")
    assert read(f) == "fresh"
    rm(f)


# ── CLI ───────────────────────────────────────────────────────────────────────

def test_cli_run_commit(tmp_path):
    f = str(tmp_path / "cli_commit.txt")
    write(f, "original")
    rc = main([
        "run", "--protect", f, "--no-journal",
        "--", "python", "-c",
        f"open({repr(f)},'w',encoding='utf-8').write('modified')",
    ])
    assert rc == 0
    assert read(f) == "modified"

def test_cli_run_rollback_on_nonzero_exit(tmp_path):
    f = str(tmp_path / "cli_rb.txt")
    write(f, "original")
    rc = main([
        "run", "--protect", f, "--no-journal",
        "--", "python", "-c",
        f"open({repr(f)},'w',encoding='utf-8').write('changed'); import sys; sys.exit(1)",
    ])
    assert rc == 1
    assert read(f) == "original"

def test_cli_status_no_orphans(monkeypatch):
    monkeypatch.setattr("safefile._cli.find_orphaned_journals", lambda: [])
    rc = main(["status"])
    assert rc == 0

def test_cli_status_with_orphans(monkeypatch):
    fake = [{"temp_dir": "/tmp/safefile_fake", "backups": {"/tmp/x.txt": "/tmp/x.bak"}, "new_paths": []}]
    monkeypatch.setattr("safefile._cli.find_orphaned_journals", lambda: fake)
    rc = main(["status"])
    assert rc == 1

def test_cli_recover_dry_run(monkeypatch):
    fake = [{"temp_dir": "/tmp/safefile_fake", "backups": {}, "new_paths": []}]
    monkeypatch.setattr("safefile._cli.find_orphaned_journals", lambda: fake)
    monkeypatch.setattr("safefile._cli.recover_orphaned", lambda verbose=False: 0)
    rc = main(["recover", "--dry-run"])
    assert rc == 0

def test_cli_run_missing_protect():
    rc = main(["run", "--", "echo", "hello"])
    assert rc == 2

def test_cli_run_verbose(tmp_path, capsys):
    f = str(tmp_path / "cli_v.txt")
    write(f, "x")
    main([
        "run", "--protect", f, "--no-journal", "--verbose",
        "--", "python", "-c", "pass",
    ])
    captured = capsys.readouterr()
    assert "[safefile]" in captured.out

def test_cli_run_hardlink_strategy(tmp_path):
    f = str(tmp_path / "cli_hl.txt")
    write(f, "original")
    rc = main([
        "run", "--protect", f, "--strategy", "hardlink", "--no-journal",
        "--", "python", "-c", f"open({repr(f)},'w',encoding='utf-8').write('modified')",
    ])
    assert rc == 0
    assert read(f) == "modified"

def test_cli_run_hardlink_rollback(tmp_path):
    f = str(tmp_path / "cli_hl_rb.txt")
    write(f, "original")
    rc = main([
        "run", "--protect", f, "--strategy", "hardlink", "--no-journal",
        "--", "python", "-c",
        f"open({repr(f)},'w',encoding='utf-8').write('changed'); import sys; sys.exit(1)",
    ])
    assert rc == 1
    assert read(f) == "original"

def test_cli_run_verify_flag(tmp_path):
    f = str(tmp_path / "cli_vf.txt")
    write(f, "original")
    rc = main([
        "run", "--protect", f, "--verify", "--no-journal",
        "--", "python", "-c", "pass",
    ])
    assert rc == 0

def test_cli_recover_yes_flag(monkeypatch):
    monkeypatch.setattr("safefile._cli.find_orphaned_journals", lambda: [
        {"temp_dir": "/tmp/fake", "backups": {}, "new_paths": [], "strategy": "copy"}
    ])
    monkeypatch.setattr("safefile._cli.recover_orphaned", lambda verbose=False: 1)
    rc = main(["recover", "--yes"])
    assert rc == 0

def test_cli_no_subcommand_prints_help(capsys):
    rc = main([])
    assert rc == 0

def test_cli_run_multiple_protect_files(tmp_path):
    fa = str(tmp_path / "cli_mpa.txt")
    fb = str(tmp_path / "cli_mpb.txt")
    write(fa, "a-orig")
    write(fb, "b-orig")
    rc = main([
        "run", "--protect", fa, fb, "--no-journal",
        "--", "python", "-c",
        f"open({repr(fa)},'w',encoding='utf-8').write('a-new'); open({repr(fb)},'w',encoding='utf-8').write('b-new')",
    ])
    assert rc == 0
    assert read(fa) == "a-new"
    assert read(fb) == "b-new"


# ── copy strategy edge cases ──────────────────────────────────────────────────

def test_empty_file_commit(tmp_path):
    f = str(tmp_path / "empty.txt")
    write(f, "")
    with transaction(f):
        write(f, "now has content")
    assert read(f) == "now has content"

def test_empty_file_rollback(tmp_path):
    f = str(tmp_path / "empty_rb.txt")
    write(f, "")
    with pytest.raises(ValueError):
        with transaction(f):
            write(f, "corrupted")
            raise ValueError
    assert read(f) == ""

def test_binary_file_preserved_on_rollback(tmp_path):
    f = str(tmp_path / "data.bin")
    original_bytes = bytes(range(256)) * 10
    with open(f, "wb") as fh:
        fh.write(original_bytes)
    with pytest.raises(RuntimeError):
        with transaction(f):
            with open(f, "wb") as fh:
                fh.write(b"\x00" * 100)
            raise RuntimeError
    with open(f, "rb") as fh:
        assert fh.read() == original_bytes

def test_unicode_content_preserved(tmp_path):
    f = str(tmp_path / "unicode.txt")
    content = "こんにちは 🌍 Ünïcödé"
    write(f, content)
    with pytest.raises(ValueError):
        with transaction(f):
            write(f, "plain ascii")
            raise ValueError
    assert read(f) == content

def test_large_text_file_rollback(tmp_path):
    f = str(tmp_path / "big.txt")
    original = "line\n" * 100_000
    write(f, original)
    with pytest.raises(RuntimeError):
        with transaction(f):
            write(f, "truncated")
            raise RuntimeError
    assert read(f) == original

def test_transaction_exception_type_preserved(tmp_path):
    f = str(tmp_path / "exc.txt")
    write(f, "x")
    with pytest.raises(KeyError):
        with transaction(f):
            raise KeyError("specific error")

def test_file_with_spaces_in_name(tmp_path):
    f = str(tmp_path / "my file name.txt")
    write(f, "original")
    with pytest.raises(ValueError):
        with transaction(f):
            write(f, "changed")
            raise ValueError
    assert read(f) == "original"

def test_deeply_nested_path_rollback(tmp_path):
    d = tmp_path / "a" / "b" / "c"
    d.mkdir(parents=True)
    f = str(d / "deep.txt")
    write(f, "deep original")
    with pytest.raises(RuntimeError):
        with transaction(f):
            write(f, "deep changed")
            raise RuntimeError
    assert read(f) == "deep original"

def test_no_files_protected_no_error():
    with transaction():
        pass

def test_same_file_listed_twice_no_double_backup(tmp_path):
    f = str(tmp_path / "dup.txt")
    write(f, "original")
    with pytest.raises(ValueError):
        with transaction(f, f):
            write(f, "changed")
            raise ValueError
    assert read(f) == "original"


# ── directory edge cases ──────────────────────────────────────────────────────

def test_deeply_nested_dir_rollback(tmp_path):
    d = str(tmp_path / "root")
    os.makedirs(os.path.join(d, "a", "b", "c"))
    write(os.path.join(d, "a", "b", "c", "deep.txt"), "deep-orig")
    write(os.path.join(d, "top.txt"), "top-orig")
    with pytest.raises(ValueError):
        with transaction(d):
            write(os.path.join(d, "a", "b", "c", "deep.txt"), "deep-changed")
            write(os.path.join(d, "top.txt"), "top-changed")
            raise ValueError
    assert read(os.path.join(d, "a", "b", "c", "deep.txt")) == "deep-orig"
    assert read(os.path.join(d, "top.txt")) == "top-orig"

def test_dir_and_file_together_rollback(tmp_path):
    d = str(tmp_path / "combo_dir")
    f = str(tmp_path / "combo_file.txt")
    os.makedirs(d)
    write(os.path.join(d, "x.txt"), "dir-orig")
    write(f, "file-orig")
    with pytest.raises(RuntimeError):
        with transaction(d, f):
            write(os.path.join(d, "x.txt"), "dir-changed")
            write(f, "file-changed")
            raise RuntimeError
    assert read(os.path.join(d, "x.txt")) == "dir-orig"
    assert read(f) == "file-orig"

def test_empty_dir_rollback(tmp_path):
    d = str(tmp_path / "empty_dir")
    os.makedirs(d)
    with pytest.raises(ValueError):
        with transaction(d):
            write(os.path.join(d, "new.txt"), "added")
            raise ValueError
    assert not os.path.exists(os.path.join(d, "new.txt"))
    assert os.path.isdir(d)


# ── savepoint edge cases ──────────────────────────────────────────────────────

def test_savepoint_on_new_file_tracks_correctly(tmp_path):
    f = str(tmp_path / "sp_new.txt")
    with transaction(f) as tx:
        sp = tx.savepoint()
        write(f, "created inside tx")
        tx.rollback_to(sp)
        assert not os.path.exists(f)

def test_savepoint_content_matches_at_creation_time(tmp_path):
    f = str(tmp_path / "sp_content.txt")
    write(f, "v0")
    with transaction(f) as tx:
        write(f, "v1")
        sp = tx.savepoint()
        write(f, "v2")
        tx.rollback_to(sp)
        assert read(f) == "v1"

def test_three_savepoints_rollback_to_middle(tmp_path):
    f = str(tmp_path / "sp3.txt")
    write(f, "v0")
    with transaction(f) as tx:
        write(f, "v1")
        _sp1 = tx.savepoint()
        write(f, "v2")
        sp2 = tx.savepoint()
        write(f, "v3")
        _sp3 = tx.savepoint()
        write(f, "v4")
        tx.rollback_to(sp2)
        assert read(f) == "v2"
    assert read(f) == "v2"

def test_savepoint_discard_cleans_temp_dir(tmp_path):
    f = str(tmp_path / "sp_discard.txt")
    write(f, "orig")
    with transaction(f) as tx:
        sp = tx.savepoint()
        sp_dir = sp._sp_dir
        assert os.path.isdir(sp_dir)
        sp.discard()
        assert not os.path.isdir(sp_dir)


# ── lazy backup edge cases ────────────────────────────────────────────────────

def test_lazy_touch_multiple_at_once(tmp_path):
    fa = str(tmp_path / "lz_ma.txt")
    fb = str(tmp_path / "lz_mb.txt")
    write(fa, "a-orig")
    write(fb, "b-orig")
    with pytest.raises(RuntimeError):
        with transaction(fa, fb, lazy=True) as tx:
            tx.touch(fa, fb)
            write(fa, "a-changed")
            write(fb, "b-changed")
            raise RuntimeError
    assert read(fa) == "a-orig"
    assert read(fb) == "b-orig"

def test_lazy_touch_idempotent(tmp_path):
    f = str(tmp_path / "lz_idem.txt")
    write(f, "orig")
    with pytest.raises(RuntimeError):
        with transaction(f, lazy=True) as tx:
            tx.touch(f)
            tx.touch(f)
            tx.touch(f)
            write(f, "changed")
            raise RuntimeError
    assert read(f) == "orig"

def test_lazy_new_file_not_touched_not_removed(tmp_path):
    f = str(tmp_path / "lz_newfile.txt")
    with pytest.raises(RuntimeError):
        with transaction(f, lazy=True):
            write(f, "created")
            raise RuntimeError
    assert os.path.exists(f)
    rm(f)


# ── async edge cases ──────────────────────────────────────────────────────────

def test_async_multiple_files_rollback():
    async def run():
        with tempfile.TemporaryDirectory() as td:
            fa = os.path.join(td, "async_fa.txt")
            fb = os.path.join(td, "async_fb.txt")
            write(fa, "a-orig")
            write(fb, "b-orig")
            with pytest.raises(ValueError):
                async with async_transaction(fa, fb):
                    write(fa, "a-changed")
                    write(fb, "b-changed")
                    raise ValueError
            assert read(fa) == "a-orig"
            assert read(fb) == "b-orig"
    asyncio.run(run())

def test_async_new_file_removed_on_rollback():
    async def run():
        with tempfile.TemporaryDirectory() as td:
            f = os.path.join(td, "async_new.txt")
            with pytest.raises(RuntimeError):
                async with async_transaction(f):
                    write(f, "created")
                    raise RuntimeError
            assert not os.path.exists(f)
    asyncio.run(run())

def test_async_hooks_fire():
    async def run():
        called = []
        with tempfile.TemporaryDirectory() as td:
            f = os.path.join(td, "async_hook.txt")
            write(f, "x")
            async with async_transaction(f, on_commit=lambda: called.append("commit")):
                write(f, "y")
            assert called == ["commit"]
    asyncio.run(run())


# ── dry-run edge cases ────────────────────────────────────────────────────────

def test_dry_run_exception_still_cleans_shadow(tmp_path):
    f = str(tmp_path / "dry_exc.txt")
    write(f, "original")
    shadow_dir = None
    with pytest.raises(ValueError):
        with transaction(f, dry_run=True) as tx:
            shadow_dir = tx.shadow_dir()
            raise ValueError
    assert not os.path.isdir(shadow_dir)
    assert read(f) == "original"

def test_dry_run_multiple_files_none_modified(tmp_path):
    fa = str(tmp_path / "dry_fa.txt")
    fb = str(tmp_path / "dry_fb.txt")
    write(fa, "a-orig")
    write(fb, "b-orig")
    with transaction(fa, fb, dry_run=True) as tx:
        with open(tx.path(fa), "w") as fh:
            fh.write("a-shadow")
        with open(tx.path(fb), "w") as fh:
            fh.write("b-shadow")
    assert read(fa) == "a-orig"
    assert read(fb) == "b-orig"

def test_dry_run_shadow_has_original_content(tmp_path):
    f = str(tmp_path / "dry_content.txt")
    write(f, "original content")
    with transaction(f, dry_run=True) as tx:
        shadow = tx.path(f)
        assert read(shadow) == "original content"


# ── verify edge cases ─────────────────────────────────────────────────────────

def test_verify_with_hardlink_strategy(tmp_path):
    f = str(tmp_path / "vf_hl.txt")
    write(f, "original")
    with pytest.raises(RuntimeError):
        with transaction(f, strategy="hardlink", verify=True):
            write(f, "changed")
            raise RuntimeError
    assert read(f) == "original"

def test_verify_multiple_files_all_restored(tmp_path):
    fa = str(tmp_path / "vf_fa.txt")
    fb = str(tmp_path / "vf_fb.txt")
    write(fa, "a-orig")
    write(fb, "b-orig")
    with pytest.raises(ValueError):
        with transaction(fa, fb, verify=True):
            write(fa, "a-changed")
            write(fb, "b-changed")
            raise ValueError
    assert read(fa) == "a-orig"
    assert read(fb) == "b-orig"


# ── journal edge cases ────────────────────────────────────────────────────────

def test_journal_updated_on_lazy_touch(tmp_path):
    from safefile._transaction import Transaction
    f = str(tmp_path / "jrn_lazy.txt")
    write(f, "orig")
    tx = Transaction(f, lazy=True, journal=True)
    watcher = tx.__enter__()
    watcher.touch(f)
    journal_file = os.path.join(tx._temp_dir, "safefile_journal.json")
    assert os.path.exists(journal_file)
    with open(journal_file) as jf:
        rec = json.load(jf)
    assert f in rec["backups"]
    tx.__exit__(None, None, None)

def test_recover_orphaned_returns_zero_when_none(monkeypatch):
    monkeypatch.setattr("safefile._journal.find_orphaned_journals", lambda: [])
    assert recover_orphaned() == 0

def test_journal_strategy_name_persisted(tmp_path):
    f = str(tmp_path / "jrn_strat.txt")
    write(f, "orig")
    with transaction(f, strategy="hardlink", journal=True) as tx:
        journal_file = os.path.join(tx._temp_dir, "safefile_journal.json")
        with open(journal_file) as jf:
            rec = json.load(jf)
        assert rec["strategy"] == "hardlink"


# ── misc ──────────────────────────────────────────────────────────────────────

def test_transaction_returns_self_when_not_lazy_or_dry(tmp_path):
    f = str(tmp_path / "self_ret.txt")
    write(f, "x")
    with transaction(f) as tx:
        from safefile._transaction import Transaction
        assert isinstance(tx, Transaction)

def test_hooks_both_set_only_one_fires(tmp_path):
    f = str(tmp_path / "both_hooks.txt")
    write(f, "x")
    log = []
    with pytest.raises(RuntimeError):
        with transaction(
            f,
            on_commit=lambda: log.append("commit"),
            on_rollback=lambda: log.append("rollback"),
        ):
            raise RuntimeError
    assert log == ["rollback"]