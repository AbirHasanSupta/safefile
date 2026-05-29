import os
import shutil
import pytest
from safefile import transaction


# ── helpers ──────────────────────────────────────────────────────────────────

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
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


# ── copy strategy (original behaviour preserved) ──────────────────────────────

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
    assert not os.path.exists("c_new.txt")
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
    assert not os.path.exists("d_new_dir")
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
    with transaction("hook_commit.txt", on_commit=lambda: called.append("commit")):
        write("hook_commit.txt", "y")
    assert called == ["commit"]
    rm("hook_commit.txt")

def test_on_rollback_hook_called():
    called = []
    write("hook_rollback.txt", "x")
    with pytest.raises(RuntimeError):
        with transaction("hook_rollback.txt", on_rollback=lambda: called.append("rollback")):
            write("hook_rollback.txt", "y")
            raise RuntimeError
    assert called == ["rollback"]
    rm("hook_rollback.txt")

def test_on_commit_hook_not_called_on_failure():
    called = []
    write("hook_no_commit.txt", "x")
    with pytest.raises(RuntimeError):
        with transaction("hook_no_commit.txt", on_commit=lambda: called.append("commit")):
            raise RuntimeError
    assert called == []
    rm("hook_no_commit.txt")

def test_on_rollback_hook_not_called_on_success():
    called = []
    write("hook_no_rb.txt", "x")
    with transaction("hook_no_rb.txt", on_rollback=lambda: called.append("rollback")):
        write("hook_no_rb.txt", "y")
    assert called == []
    rm("hook_no_rb.txt")


# ── invalid strategy ──────────────────────────────────────────────────────────

def test_invalid_strategy_raises():
    with pytest.raises(ValueError, match="Unknown strategy"):
        transaction("any.txt", strategy="magic")