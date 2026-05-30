import asyncio
import os
import shutil
import pytest
from safefile import transaction, async_transaction


# ── helpers ───────────────────────────────────────────────────────────────────

def write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w") as f:
        f.write(text)

def read(path):
    with open(path) as f:
        return f.read()

def rm(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


# ── v0.2: copy strategy ───────────────────────────────────────────────────────

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
    rm("c_f1.txt"); rm("c_f2.txt")


# ── v0.2: hardlink strategy ───────────────────────────────────────────────────

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


# ── v0.2: directory support ───────────────────────────────────────────────────

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


# ── v0.2: hooks ───────────────────────────────────────────────────────────────

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
    with pytest.raises(ValueError, match="Unknown strategy"):
        transaction("any.txt", strategy="magic")


# ── v0.3: savepoints ─────────────────────────────────────────────────────────

def test_savepoint_rollback_partial():
    write("sp_a.txt", "a-orig")
    write("sp_b.txt", "b-orig")
    with transaction("sp_a.txt", "sp_b.txt") as tx:
        write("sp_a.txt", "a-step1")
        sp = tx.savepoint()
        write("sp_b.txt", "b-step2")
        tx.rollback_to(sp)
        # b should be back to orig, a stays at step1
        assert read("sp_a.txt") == "a-step1"
        assert read("sp_b.txt") == "b-orig"
        write("sp_b.txt", "b-final")
    assert read("sp_a.txt") == "a-step1"
    assert read("sp_b.txt") == "b-final"
    rm("sp_a.txt"); rm("sp_b.txt")

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
        sp2 = tx.savepoint()
        write("sp_m.txt", "v3")
        tx.rollback_to(sp1)
        # rolling back to sp1 should discard sp2
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


# ── v0.3: lazy backup ─────────────────────────────────────────────────────────

def test_lazy_backup_only_touched_files():
    write("lz_a.txt", "a-orig")
    write("lz_b.txt", "b-orig")
    with pytest.raises(RuntimeError):
        with transaction("lz_a.txt", "lz_b.txt", lazy=True) as tx:
            tx.touch("lz_a.txt")          # only a is backed up
            write("lz_a.txt", "a-changed")
            write("lz_b.txt", "b-changed")  # b changed but NOT touch()'d
            raise RuntimeError
    assert read("lz_a.txt") == "a-orig"   # a restored
    assert read("lz_b.txt") == "b-changed"  # b not backed up, stays changed
    rm("lz_a.txt"); rm("lz_b.txt")

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
        with transaction("lz_skip.txt", lazy=True) as tx:
            write("lz_skip.txt", "changed")
            raise RuntimeError
    # never touch()'d → never backed up → not restored
    assert read("lz_skip.txt") == "changed"
    rm("lz_skip.txt")


# ── v0.3: async ───────────────────────────────────────────────────────────────

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


# ── v0.3: dry run ─────────────────────────────────────────────────────────────

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
    shadow_path = None
    with transaction("dry_clean.txt", dry_run=True) as tx:
        shadow_path = tx.path("dry_clean.txt")
        shadow_dir = tx.shadow_dir()
    assert not os.path.isdir(shadow_dir)
    rm("dry_clean.txt")

def test_dry_run_new_file_not_created():
    with transaction("dry_new.txt", dry_run=True) as tx:
        shadow = tx.path("dry_new.txt")
        with open(shadow, "w") as f:
            f.write("something")
    assert not os.path.exists("dry_new.txt")


# ── v0.3: checksum verify ─────────────────────────────────────────────────────

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


# ── v0.3: streaming / progress ────────────────────────────────────────────────

def test_progress_callback_called_for_large_files(tmp_path):
    large = str(tmp_path / "large.bin")
    with open(large, "wb") as f:
        f.write(b"x" * (6 * 1024 * 1024))  # 6 MB
    progress = []
    with pytest.raises(RuntimeError):
        with transaction(
            large,
            chunk_size=2 * 1024 * 1024,   # 2 MB chunks → 3 callbacks
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