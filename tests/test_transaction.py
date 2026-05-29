import os
import pytest
from safefile import transaction

def test_commit_success():
    with open("test_commit.txt", "w") as f:
        f.write("original")

    with transaction("test_commit.txt"):
        with open("test_commit.txt", "w") as f:
            f.write("modified")

    with open("test_commit.txt") as f:
        assert f.read() == "modified"
    os.remove("test_commit.txt")

def test_rollback_on_exception():
    with open("test_rollback.txt", "w") as f:
        f.write("original")

    with pytest.raises(ValueError):
        with transaction("test_rollback.txt"):
            with open("test_rollback.txt", "w") as f:
                f.write("changed")
            raise ValueError("something went wrong")

    with open("test_rollback.txt") as f:
        assert f.read() == "original"
    os.remove("test_rollback.txt")

def test_rollback_removes_new_files():
    assert not os.path.exists("new_file.txt")
    with pytest.raises(RuntimeError):
        with transaction("new_file.txt"):
            with open("new_file.txt", "w") as f:
                f.write("something")
            raise RuntimeError("fail")

    assert not os.path.exists("new_file.txt")

def test_multiple_files():
    with open("file1.txt", "w") as f:
        f.write("one")
    with open("file2.txt", "w") as f:
        f.write("two")

    with pytest.raises(ValueError):
        with transaction("file1.txt", "file2.txt"):
            with open("file1.txt", "w") as f:
                f.write("modified_one")
            with open("file2.txt", "w") as f:
                f.write("modified_two")
            raise ValueError()

    with open("file1.txt") as f:
        assert f.read() == "one"
    with open("file2.txt") as f:
        assert f.read() == "two"

    os.remove("file1.txt")
    os.remove("file2.txt")