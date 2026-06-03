import argparse
import subprocess
import sys
import os
from typing import Callable, List, Optional

from ._journal import recover_orphaned, find_orphaned_journals
from ._transaction import Transaction
from ._strategies import _STRATEGIES
from ._exceptions import SafefileError


def _fmt_bytes(n: int) -> str:
    size: float = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _make_progress_cb(label: str) -> Callable[[int], None]:
    def _cb(pct: int) -> None:
        sys.stderr.write(f"\r  backing up {label}: {pct:3d}%")
        if pct >= 100:
            sys.stderr.write("\n")
        sys.stderr.flush()
    return _cb


def cmd_run(args: argparse.Namespace) -> int:
    if not args.command:
        print("error: no command given after --", file=sys.stderr)
        return 2

    filepaths = args.protect or []
    if not filepaths:
        print("error: --protect requires at least one file", file=sys.stderr)
        return 2

    strategy = args.strategy
    verify   = args.verify
    journal  = not args.no_journal

    if args.verbose:
        print(f"[safefile] protecting: {', '.join(filepaths)}")
        print(f"[safefile] strategy: {strategy}  verify: {verify}  journal: {journal}")

    # Build per-file transactions when --progress is requested for multiple
    # files so each file gets its own progress bar.  For a single file (or
    # when --progress is not set) use a single combined transaction.
    if args.progress and len(filepaths) > 1:
        return _run_multi_progress(
            filepaths, args.command, strategy, verify, journal, args.verbose
        )

    on_progress = None
    if args.progress and len(filepaths) == 1:
        on_progress = _make_progress_cb(os.path.basename(filepaths[0]))

    tx = Transaction(
        *filepaths,
        strategy=strategy,
        verify=verify,
        journal=journal,
        on_progress=on_progress,
        on_commit=(lambda: print("[safefile] committed")) if args.verbose else None,
        on_rollback=(lambda: print("[safefile] rolled back — files restored")) if args.verbose else None,
    )

    result = None
    try:
        with tx:
            result = subprocess.run(args.command)
            if result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, args.command)
    except subprocess.CalledProcessError:
        pass
    except SafefileError as exc:
        print(f"[safefile] error: {exc}", file=sys.stderr)
        return 1

    return result.returncode if result is not None else 1


def _run_multi_progress(filepaths: List[str],
                        command: List[str],
                        strategy: str,
                        verify: bool,
                        journal: bool,
                        verbose: bool) -> int:
    """
    Back up each file individually with its own progress bar, then run the
    command inside a single combined transaction that reuses those backups.

    Strategy: use one Transaction for all files; the on_progress callback
    tracks which file is currently being backed up by hooking _register.
    We accomplish this by backing up sequentially and printing progress per
    file before the command runs.
    """
    # We create one transaction but monkey-patch on_progress per file by
    # using individual single-file transactions for backup, then combine
    # into one outer transaction that shares the same temp dir.
    #
    # Simplest correct approach: run each file through its own Transaction
    # for the backup phase only, collect their temp dirs, then restore all
    # on failure.  This is clean and avoids coupling to internals.
    import tempfile
    import shutil
    from ._strategies import get_strategy
    from ._journal import write_journal, mark_committed

    # One combined transaction — progress per-file via pre-registration
    transactions = []
    for fp in filepaths:
        cb = _make_progress_cb(os.path.basename(fp))
        t = Transaction(
            fp,
            strategy=strategy,
            verify=verify,
            journal=journal,
            on_progress=cb,
        )
        transactions.append(t)

    # Enter all transactions (backs up each file with its progress bar)
    entered = []
    try:
        for t in transactions:
            t.__enter__()
            entered.append(t)
    except Exception as exc:
        print(f"[safefile] backup failed: {exc}", file=sys.stderr)
        for t in reversed(entered):
            try:
                t.__exit__(Exception, exc, None)
            except Exception:
                pass
        return 1

    if verbose:
        print("[safefile] all backups complete — running command")

    result = None
    failed = False
    try:
        result = subprocess.run(command)
        if result.returncode != 0:
            failed = True
    except Exception:
        failed = True

    if failed:
        if verbose:
            print("[safefile] command failed — rolling back all files")
        for t in reversed(entered):
            try:
                t.__exit__(Exception, Exception("nonzero exit"), None)
            except SafefileError as exc:
                print(f"[safefile] rollback error: {exc}", file=sys.stderr)
    else:
        if verbose:
            print("[safefile] committed")
        for t in entered:
            try:
                t.__exit__(None, None, None)
            except SafefileError as exc:
                print(f"[safefile] commit error: {exc}", file=sys.stderr)

    return result.returncode if result is not None else 1


def cmd_recover(args: argparse.Namespace) -> int:
    orphans = find_orphaned_journals()
    if not orphans:
        print("No orphaned transactions found.")
        return 0

    print(f"Found {len(orphans)} orphaned transaction(s):")
    for o in orphans:
        print(f"  {o['temp_dir']}")
        for path in o.get("backups", {}):
            print(f"    restore ← {path}")
        for path in o.get("new_paths", []):
            print(f"    delete  ← {path}")

    if args.dry_run:
        print("(dry-run — no changes made)")
        return 0

    if not args.yes:
        ans = input("Recover all? [y/N] ").strip().lower()
        if ans != "y":
            print("Aborted.")
            return 1

    count = recover_orphaned(verbose=args.verbose)
    print(f"Recovered {count} transaction(s).")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    orphans = find_orphaned_journals()
    if not orphans:
        print("safefile: no orphaned transactions.")
        return 0
    print(f"safefile: {len(orphans)} orphaned transaction(s) pending recovery:")
    for o in orphans:
        files = list(o.get("backups", {}).keys())
        print(f"  [{o['temp_dir']}]  files: {', '.join(files) or '(none)'}")
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="safefile",
        description="Atomic, transactional file protection for shell commands.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    # ── safefile run ──────────────────────────────────────────────────────────
    p_run = sub.add_parser(
        "run",
        help="Run a command with file protection. Files are restored if the command fails.",
        usage="safefile run --protect FILE [FILE ...] [options] -- COMMAND [ARGS ...]",
    )
    p_run.add_argument(
        "--protect", "-p",
        nargs="+",
        metavar="FILE",
        help="Files or directories to protect",
    )
    p_run.add_argument(
        "--strategy", "-s",
        default="copy",
        choices=list(_STRATEGIES),
        help="Backup strategy (default: copy)",
    )
    p_run.add_argument(
        "--verify",
        action="store_true",
        help="SHA-256 verify backup integrity after restore",
    )
    p_run.add_argument(
        "--no-journal",
        action="store_true",
        help="Disable crash-recovery journal",
    )
    p_run.add_argument(
        "--progress",
        action="store_true",
        help="Show backup progress for large files",
    )
    p_run.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print transaction lifecycle messages",
    )
    p_run.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run (after --)",
    )

    # ── safefile recover ──────────────────────────────────────────────────────
    p_rec = sub.add_parser(
        "recover",
        help="Restore files from orphaned crash-recovery journals.",
    )
    p_rec.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    p_rec.add_argument("--dry-run", "-n", action="store_true", help="Show what would be recovered without doing it")
    p_rec.add_argument("--verbose", "-v", action="store_true")

    # ── safefile status ───────────────────────────────────────────────────────
    sub.add_parser(
        "status",
        help="List any pending orphaned transactions (exit 1 if any found).",
    )

    parsed = parser.parse_args(argv)

    if parsed.subcommand == "run":
        cmd = parsed.command
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        parsed.command = cmd
        return cmd_run(parsed)
    elif parsed.subcommand == "recover":
        return cmd_recover(parsed)
    elif parsed.subcommand == "status":
        return cmd_status(parsed)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
