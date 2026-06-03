import json
import os
import shutil
import tempfile
from typing import Dict, List, Set, Any

from ._exceptions import JournalError


_JOURNAL_FILENAME = "safefile_journal.json"


def _journal_path(temp_dir: str) -> str:
    return os.path.join(temp_dir, _JOURNAL_FILENAME)


def write_journal(
    temp_dir: str,
    filepaths: List[str],
    backups: Dict[str, str],
    new_paths: Set[str],
    dirs: Set[str],
    strategy: str,
) -> None:
    record = {
        "temp_dir": temp_dir,
        "filepaths": list(filepaths),
        "backups": backups,
        "new_paths": list(new_paths),
        "dirs": list(dirs),
        "strategy": strategy,
        "status": "open",
    }
    path = _journal_path(temp_dir)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        os.replace(tmp, path)
    except OSError as exc:
        raise JournalError(path, str(exc)) from exc


def mark_committed(temp_dir: str) -> None:
    path = _journal_path(temp_dir)
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        record["status"] = "committed"
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        os.replace(tmp, path)
    except (OSError, json.JSONDecodeError) as exc:
        raise JournalError(path, str(exc)) from exc


def find_orphaned_journals() -> List[Dict[str, Any]]:
    orphans = []
    tmp_root = tempfile.gettempdir()
    try:
        entries = os.listdir(tmp_root)
    except PermissionError:
        return []
    for entry in entries:
        if not entry.startswith("safefile_"):
            continue
        candidate = os.path.join(tmp_root, entry)
        journal = os.path.join(candidate, _JOURNAL_FILENAME)
        if not os.path.isfile(journal):
            continue
        try:
            with open(journal, encoding="utf-8") as f:
                record = json.load(f)
            if record.get("status") == "open":
                orphans.append(record)
        except (json.JSONDecodeError, KeyError):
            continue
    return orphans


def recover_orphaned(verbose: bool = False) -> int:
    from ._strategies import get_strategy

    orphans = find_orphaned_journals()
    recovered = 0

    for record in orphans:
        temp_dir = record["temp_dir"]
        backups: Dict[str, str] = record["backups"]
        new_paths: List[str] = record["new_paths"]
        dirs: List[str] = record["dirs"]
        strategy_name: str = record.get("strategy", "copy")

        if verbose:
            print(f"[safefile] recovering orphaned transaction: {temp_dir}")

        try:
            strategy = get_strategy(strategy_name)
            for original, backup in backups.items():
                if not os.path.exists(backup):
                    continue
                if original in dirs:
                    strategy.restore_dir(backup, original)
                else:
                    strategy.restore(backup, original)
                    if verbose:
                        print(f"  restored: {original}")

            for fp in new_paths:
                if os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                elif os.path.exists(fp):
                    os.remove(fp)
                    if verbose:
                        print(f"  removed new file: {fp}")

            if os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir)

            recovered += 1
        except Exception as exc:
            if verbose:
                print(f"  [safefile] recovery failed for {temp_dir}: {exc}")

    return recovered
